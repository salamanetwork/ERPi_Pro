import hashlib
import hmac
import secrets
import uuid
from datetime import timedelta
from urllib.parse import urlencode
from django.conf import settings
from django.contrib.auth.hashers import make_password,check_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction,IntegrityError
from django.utils import timezone
from rest_framework.exceptions import ValidationError as APIValidation, AuthenticationFailed
from backbone.models import AuditEvent
from backbone.registry import enabled_modules
from publication.models import PublicationSpace
from .models import ExternalIdentity,ExternalMembership,ExternalSession,IdentityChallenge,IdentityAudit
from .security import digest,backend,decode,session_for

DUMMY=make_password("not-a-valid-login-password")

def event(identity,action,target="",staff=None):
    IdentityAudit.objects.create(identity=identity,staff_actor=staff,event=action,target=str(target))
    AuditEvent.objects.create(actor_user_id=staff.pk if staff else None,
        actor=staff.get_username() if staff else "external:"+str(identity.pk),action="public_identity."+action,target=str(target or identity.pk))

def text(data,field,maximum=120):
    value=data.get(field)
    if not isinstance(value,str) or not value.strip(): raise APIValidation({field:["This field is required."]})
    if len(value.strip())>maximum: raise APIValidation({field:["This value is too long."]})
    return value.strip()

def email(data):
    value=text(data,"email",254).casefold()
    try: validate_email(value)
    except ValidationError: raise APIValidation({"email":["Enter a valid email address."]})
    return value

def password(data):
    value=data.get("password")
    if not isinstance(value,str) or not value or len(value)>128: raise APIValidation({"password":["Enter a password up to 128 characters."]})
    try: validate_password(value)
    except ValidationError as error: raise APIValidation({"password":error.messages})
    return value

def space(data):
    code=text(data,"space",50)
    row=PublicationSpace.objects.filter(code=code,active=True,internal_site__active=True).first()
    if not row: raise APIValidation({"space":["Select an active public site."]})
    return row

def challenge_token(row):
    payload=f"challenge:{row.id}:{row.purpose}:{row.expires_at.isoformat()}"
    return hmac.new(settings.ERP_CUSTOMER_JWT_KEY.encode(),payload.encode(),hashlib.sha256).hexdigest()

def challenge(identity,site,purpose):
    now=timezone.now()
    IdentityChallenge.objects.filter(identity=identity,space=site,purpose=purpose,consumed_at=None).update(consumed_at=now)
    row=IdentityChallenge(identity=identity,space=site,purpose=purpose,expires_at=now+timedelta(hours=24 if purpose=="verify" else 1),next_attempt_at=now)
    row.token_hash=digest(challenge_token(row));row.save()
    return row

@transaction.atomic
def register(data):
    site=space(data);address=email(data);secret=password(data);name=text(data,"display_name")
    hashed=make_password(secret)
    identity=ExternalIdentity.objects.select_for_update().filter(normalized_email=address).first()
    if identity: return
    try:
        with transaction.atomic():
            identity=ExternalIdentity.objects.create(normalized_email=address,password_hash=hashed,display_name=name)
    except IntegrityError: return
    ExternalMembership.objects.create(identity=identity,space=site)
    challenge(identity,site,"verify");event(identity,"registered",site.pk)

def issue(row):
    now=timezone.now();jti=secrets.token_urlsafe(32)
    row.refresh_jti_hash=digest(jti);row.save(update_fields=["refresh_jti_hash"])
    base={"sub":str(row.identity_id),"sid":str(row.pk),"space":row.space_id,"ver":row.identity.auth_version,"iat":int(now.timestamp())}
    access=backend().encode({**base,"jti":secrets.token_hex(16),"token_type":"access","exp":int((now+timedelta(minutes=5)).timestamp())})
    refresh=backend().encode({**base,"jti":jti,"token_type":"refresh","exp":int(row.expires_at.timestamp())})
    return {"access":access,"expires_in":300,"token_type":"Bearer"},refresh

@transaction.atomic
def login(data):
    site=space(data);address=email(data)
    identity=ExternalIdentity.objects.select_for_update().filter(normalized_email=address).first()
    secret=data.get("password","")
    valid=isinstance(secret,str) and len(secret)<=128 and check_password(secret,identity.password_hash if identity else DUMMY)
    if not valid or not identity or identity.status!="active" or not identity.verified_at or not ExternalMembership.objects.filter(identity=identity,space=site,active=True).exists():
        raise AuthenticationFailed("Invalid credentials or unverified account.")
    row=ExternalSession.objects.create(identity=identity,space=site,refresh_jti_hash=secrets.token_hex(32),expires_at=timezone.now()+timedelta(days=7))
    result=issue(row);event(identity,"login",row.pk)
    return result

def rotate(raw):
    payload=decode(raw,"refresh")
    replay=False
    with transaction.atomic():
        # Identity first: same lock order as reset/logout-all, then session.
        identity=ExternalIdentity.objects.select_for_update().filter(pk=payload["sub"]).first()
        if not identity: raise AuthenticationFailed("Invalid identity.")
        row=session_for(payload,lock=True)
        if not hmac.compare_digest(row.refresh_jti_hash,digest(payload["jti"])):
            row.revoked_at=timezone.now();row.save(update_fields=["revoked_at"])
            event(identity,"refresh_reuse",row.pk);replay=True
        else: result=issue(row)
    if replay: raise AuthenticationFailed("Refresh token reuse detected. Sign in again.")
    return result

def consume(data,purpose):
    try: pk=uuid.UUID(str(data.get("challenge_id","")))
    except ValueError: raise APIValidation({"token":["Invalid or expired link."]})
    preliminary=IdentityChallenge.objects.filter(pk=pk,purpose=purpose).first()
    if not preliminary: raise APIValidation({"token":["Invalid or expired link."]})
    with transaction.atomic():
        identity=ExternalIdentity.objects.select_for_update().get(pk=preliminary.identity_id)
        row=IdentityChallenge.objects.select_for_update().get(pk=pk)
        raw=data.get("token","")
        if not isinstance(raw,str) or len(raw)>200 or row.consumed_at or row.expires_at<=timezone.now() or not hmac.compare_digest(row.token_hash,digest(raw)) or not row.space.active or identity.status=="disabled":
            raise APIValidation({"token":["Invalid or expired link."]})
        if purpose=="reset":
            identity.password_hash=make_password(password(data));identity.auth_version+=1
            ExternalSession.objects.filter(identity=identity,revoked_at=None).update(revoked_at=timezone.now())
        else: identity.status="active";identity.verified_at=timezone.now()
        identity.revision+=1;identity.save()
        row.consumed_at=timezone.now();row.save(update_fields=["consumed_at"])
        event(identity,"password_reset" if purpose=="reset" else "email_verified",row.pk)

@transaction.atomic
def request_challenge(data,purpose):
    site=space(data);address=email(data)
    identity=ExternalIdentity.objects.select_for_update().filter(normalized_email=address).first()
    if identity and identity.status!="disabled" and ExternalMembership.objects.filter(identity=identity,space=site,active=True).exists() and (purpose=="reset" and identity.status=="active" or purpose=="verify" and not identity.verified_at):
        challenge(identity,site,purpose)

def logout(row,all_sessions=False):
    with transaction.atomic():
        identity=ExternalIdentity.objects.select_for_update().get(pk=row.identity_id)
        if all_sessions:
            identity.auth_version+=1;identity.save(update_fields=["auth_version"])
            ExternalSession.objects.filter(identity=identity,revoked_at=None).update(revoked_at=timezone.now())
        else: ExternalSession.objects.filter(pk=row.pk).update(revoked_at=timezone.now())
        event(identity,"logout_all" if all_sessions else "logout",row.pk)
