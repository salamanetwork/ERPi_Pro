from datetime import timedelta
from django.db import models
import hashlib
import uuid
from django.core.exceptions import ValidationError as DjangoValidationError
from django.conf import settings
from django.db import transaction
from django.middleware.csrf import CsrfViewMiddleware
from django.utils import timezone
from rest_framework_simplejwt.backends import TokenBackend
from rest_framework_simplejwt.exceptions import TokenBackendError
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from backbone.registry import enabled_modules
from .models import ExternalSession, ExternalMembership, AuthRateBucket

def digest(value): return hashlib.sha256(value.encode()).hexdigest()
def backend():
    return TokenBackend(algorithm="HS256",signing_key=settings.ERP_CUSTOMER_JWT_KEY,
                        audience="erpi.customer",issuer="erpi.public")
def decode(value,kind):
    try:
        payload=backend().decode(value,verify=True)
        if payload.get("token_type")!=kind: raise ValueError()
        if not all(k in payload for k in ("sub","sid","space","ver","jti","exp","iat")): raise ValueError()
        uuid.UUID(payload["sub"]);uuid.UUID(payload["sid"])
        if type(payload["space"]) is not int or type(payload["ver"]) is not int: raise ValueError()
        return payload
    except (TokenBackendError,ValueError,TypeError,KeyError,AttributeError): raise AuthenticationFailed("Invalid or expired token.")

def session_for(payload,lock=False):
    query=ExternalSession.objects.select_related("identity","space")
    if lock: query=query.select_for_update(of=("self",))
    try: row=query.get(pk=payload["sid"],identity_id=payload["sub"],space_id=payload["space"])
    except (ExternalSession.DoesNotExist,ValueError,TypeError,DjangoValidationError): raise AuthenticationFailed("Invalid session.")
    if row.revoked_at or row.expires_at<=timezone.now() or row.identity.status!="active" or not row.identity.verified_at or row.identity.auth_version!=payload["ver"] or not row.space.active or not row.space.internal_site.active:
        raise AuthenticationFailed("Session revoked.")
    if not ExternalMembership.objects.filter(identity=row.identity,space=row.space,active=True).exists():
        raise AuthenticationFailed("Membership inactive.")
    return row

class ExternalPrincipal:
    is_authenticated=True
    is_anonymous=False
    is_staff=False
    is_superuser=False
    def __init__(self,session):
        self.session=session;self.identity=session.identity;self.pk=session.identity_id
    def get_username(self): return str(self.pk)

class CustomerAuthentication(BaseAuthentication):
    def authenticate_header(self,request): return "Bearer"
    def authenticate(self,request):
        value=request.headers.get("Authorization","")
        if not value: return None
        if not {"publication","public_identity"}.issubset(enabled_modules()): raise AuthenticationFailed("Service disabled.")
        parts=value.split()
        if len(parts)!=2 or parts[0]!="Bearer": raise AuthenticationFailed("Bearer token required.")
        row=session_for(decode(parts[1],"access"))
        ExternalSession.objects.filter(pk=row.pk).filter(models.Q(last_activity=None)|models.Q(last_activity__lt=timezone.now()-timedelta(minutes=5))).update(last_activity=timezone.now())
        return ExternalPrincipal(row),row

class CSRFCheck(CsrfViewMiddleware):
    def _reject(self,request,reason): return reason

def enforce_csrf(request):
    check=CSRFCheck(lambda r:None)
    check.process_request(request._request)
    if check.process_view(request._request,lambda r:None,(),{}):
        from rest_framework.exceptions import PermissionDenied
        raise PermissionDenied("CSRF verification failed.")

def rate_limit(request,scope,email=""):
    # DB-backed: authentication remains protected when Redis is unavailable.
    # REMOTE_ADDR only; a public reverse proxy must overwrite, never trust user X-Forwarded-For.
    minute=int(timezone.now().timestamp())//60
    keys=[(scope+":ip:"+request.META.get("REMOTE_ADDR","unknown"),30)]
    if email: keys.append((scope+":email:"+email,5))
    from rest_framework.exceptions import Throttled
    for raw,maximum in keys:
        with transaction.atomic():
            row,_=AuthRateBucket.objects.get_or_create(key=digest(raw),defaults={"window":minute,"count":0})
            row=AuthRateBucket.objects.select_for_update().get(pk=row.pk)
            if row.window!=minute: row.window=minute;row.count=0
            row.count+=1;row.save()
            excess=row.count>maximum
        if excess: raise Throttled(wait=60)
