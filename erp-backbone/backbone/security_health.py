import math,os,time
from collections import Counter
from datetime import datetime,timezone as tz
from pathlib import Path
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.db import transaction
from django.utils import timezone
from public_identity.models import ExternalSession
from .access import audit
from .backups import archives
from .http import endpoint,body
from .models import Authenticator, OutboxEvent
from . import mfa
from .validation import Problem

class ActivityMiddleware:
    def __init__(self,get_response):self.get_response=get_response
    def __call__(self,request):
        response=self.get_response(request)
        if request.user.is_authenticated and response.status_code<400 and time.time()-request.session.get('erp_activity',0)>300:
            request.session['erp_activity']=int(time.time())
        return response

def authenticated_sessions():
    for session in Session.objects.filter(expire_date__gt=timezone.now()).iterator():
        data=session.get_decoded()
        if data.get('_auth_user_id'):yield session,data

def backup_time(key):
    directory=os.getenv(key)
    if not directory:return None
    try:
        paths=archives(Path(directory))
        return datetime.fromtimestamp(paths[0].stat().st_mtime,tz.utc) if paths else None
    except OSError:return None

def strong(value):
    if len(value)<32:return False
    freq=Counter(value)
    return -sum(n*math.log2(n/len(value)) for n in freq.values())>=128

@endpoint('GET',admin=True)
def health(request):
    users=get_user_model().objects.filter(is_active=True)
    enrolled=Authenticator.objects.filter(user__in=users).exclude(encrypted_key='').count()
    active=list(authenticated_sessions())
    last=max((d.get('erp_activity',0) for _,d in active),default=0)
    ext=ExternalSession.objects.filter(revoked_at=None,expires_at__gt=timezone.now(),identity__status='active',space__active=True,space__internal_site__active=True)
    latest=backup_time('ERP_HEALTH_BACKUP_DIR')
    pre=backup_time('ERP_HEALTH_PREUPGRADE_DIR')
    return {'keys':{'connector_strength':strong(settings.ERP_CONNECTOR_KEY),'connector_separate':bool(settings.ERP_CONNECTOR_KEY) and settings.ERP_CONNECTOR_KEY not in (settings.ERP_MFA_KEY,settings.SECRET_KEY,settings.ERP_CUSTOMER_JWT_KEY),'separate':settings.SECRET_KEY!=settings.ERP_CUSTOMER_JWT_KEY,'staff_strength':strong(settings.SECRET_KEY),'portal_strength':strong(settings.ERP_CUSTOMER_JWT_KEY),'portal_key_configured':bool(os.getenv('ERP_CUSTOMER_JWT_KEY'))},
        'mfa':{'active_users':users.count(),'enrolled':enrolled,'enforced':sum(mfa.required(u) for u in users),'all':settings.ERP_MFA_ALL},
        'sessions':{'staff':len(active),'external':ext.count(),'staff_last':datetime.fromtimestamp(last,tz.utc) if last else None,'external_last':ext.exclude(last_activity=None).order_by('-last_activity').values_list('last_activity',flat=True).first()},
        'outbox':queue_counts(),
        'backup':{'latest':latest,'pre_upgrade':pre,'retention':os.getenv('ERP_HEALTH_RETAIN'),'recent':bool(latest and (timezone.now()-latest).total_seconds()<172800)},
        'runtime':{'debug':settings.DEBUG,'hosts':settings.ALLOWED_HOSTS,'csrf':'django.middleware.csrf.CsrfViewMiddleware' in settings.MIDDLEWARE,'secure_cookie':settings.SESSION_COOKIE_SECURE,'https':request.is_secure(),'frame_options':settings.X_FRAME_OPTIONS,'portal_csp':"nonce"}}

@endpoint('POST',admin=True)
@transaction.atomic
def revoke(request):
    kind=body(request).get('kind')
    if kind=='external':count=ExternalSession.objects.filter(revoked_at=None,expires_at__gt=timezone.now()).update(revoked_at=timezone.now())
    elif kind=='staff':
        ids=[s.pk for s,_ in authenticated_sessions()]
        count=Session.objects.filter(pk__in=ids).delete()[0]
        request.session.flush()
    else:raise Problem('invalid_kind',field='kind')
    audit(request.user,'security.revoke_all',f'{kind}: {count}')
    return {'revoked':count}

def queue_counts():
    from django.db.models import Q
    pending=Q(moduleeventdelivery__completed_at=None,moduleeventdelivery__isnull=False)|Q(notificationdelivery__state__in=['pending','queued','running'])
    failed=Q(notificationdelivery__state='failed')|Q(moduleeventdelivery__completed_at=None,moduleeventdelivery__last_error__gt='')
    return {'pending':OutboxEvent.objects.filter(pending).distinct().count(),'failed':OutboxEvent.objects.filter(failed).distinct().count()}
