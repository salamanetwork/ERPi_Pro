import hashlib,hmac,secrets,time
from django.core.exceptions import ValidationError
from django.db import IntegrityError,transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from rest_framework.exceptions import Throttled
from public_identity.security import rate_limit
from .http import body,endpoint,Problem
from .access import audit
from .correlation import record
from .models import ApiConnector,WebhookEndpoint,OutboundApiLog,WebhookDelivery
from .services.connector_client import cipher,call_connector,resolve,path_check,header_check
from .services.pagination import paginate,search_rows
from .services.outbox import enqueue

CONNECTOR=('slug','name','site_id','base_url','health_path','auth_type','auth_header_name','timeout_seconds','max_retries','active')
HOOK=('slug','name','site_id','connector_id','signature_header','timestamp_header','tolerance_seconds','active')
def listing(req,model,fields,search):
    q=req.GET.copy()
    q['q']=q.get('search',q.get('q',''))
    return paginate(req,search_rows(model.objects.all(),q,search).values(*fields).order_by('-id'),lambda x:x)

def private(data,status=200):
    r=JsonResponse(data,status=status)
    r['Cache-Control']='no-store'
    return r

@endpoint('GET','POST','PATCH',admin=True)
@transaction.atomic
def registry(request,kind,slug=None):
    hook=kind=='webhooks'
    model,fields=(WebhookEndpoint,HOOK) if hook else (ApiConnector,CONNECTOR)
    visible=(*fields,'id','revision')
    if request.method=='GET' and not slug:return listing(request,model,visible,('name','slug'))
    if request.method!=('PATCH' if slug else 'POST'):raise Problem('method_not_allowed',405)
    d=body(request)
    r=model.objects.select_for_update().filter(slug=slug).first() if slug else model()
    if r is None:raise Problem('not_found',404)
    if slug and d.get('revision')!=r.revision:raise Problem('revision_conflict',409)
    for k in fields:
        if k in d and not (slug and k=='slug'):setattr(r,k,d[k])
    secret=None
    try:
        if hook:
            if not slug:
                secret=secrets.token_urlsafe(32)
                r.encrypted_secret=cipher().encrypt(secret.encode()).decode()
            header_check(r.signature_header)
            if r.timestamp_header:header_check(r.timestamp_header)
            if not 1<=int(r.tolerance_seconds)<=300:raise ValueError()
        else:
            resolve(r.base_url)
            path_check(r.health_path)
            header_check(r.auth_header_name)
            if not 1<=int(r.timeout_seconds)<=60 or not 0<=int(r.max_retries)<=5:raise ValueError()
            if d.get('credentials'):
                v=d['credentials']
                if not isinstance(v,str) or len(v)>4096 or not v.isascii() or any(ord(c)<32 for c in v):raise ValueError()
                r.encrypted_credentials=cipher().encrypt(v.encode()).decode()
            if r.auth_type!='none' and not r.encrypted_credentials:raise Problem('credentials_required',field='credentials')
        r.full_clean()
        if hook and r.connector_id and r.connector.site_id not in (None,r.site_id):raise Problem('scope_conflict')
        if not hook and slug and r.site_id and r.webhookendpoint_set.exclude(site_id=r.site_id).exists():raise Problem('scope_conflict')
        if slug:r.revision+=1
        r.save()
    except (ValidationError,TypeError,ValueError,IntegrityError):raise Problem('invalid_configuration') from None
    audit(request.user,'gateway.saved',kind+'/'+r.slug)
    result={k:getattr(r,k) for k in visible}
    if secret:result['signing_secret']=secret
    return private(result,200 if slug else 201)

@endpoint('POST',admin=True)
def probe(request,slug):
    c=ApiConnector.objects.filter(slug=slug,active=True).first()
    if not c:raise Problem('not_found',404)
    audit(request.user,'gateway.probe',slug)
    result=call_connector(slug,'HEAD',c.health_path,site_id=c.site_id)
    return {k:result[k] for k in ('status','duration_ms')}

@endpoint('POST',admin=True)
@transaction.atomic
def rotate(request,slug):
    r=WebhookEndpoint.objects.select_for_update().filter(slug=slug).first()
    if not r:raise Problem('not_found',404)
    if body(request).get('revision')!=r.revision:raise Problem('revision_conflict',409)
    secret=secrets.token_urlsafe(32)
    r.encrypted_secret=cipher().encrypt(secret.encode()).decode()
    r.revision+=1
    r.save()
    audit(request.user,'webhook.rotate',slug)
    return private(dict(signing_secret=secret,revision=r.revision))

@endpoint('GET',admin=True)
def logs(request,kind):
    if kind=='logs':return listing(request,OutboundApiLog,('id','connector__slug','method','path','status_code','duration_ms','success','error_code','created_at'),('connector__slug','method','path','error_code'))
    return listing(request,WebhookDelivery,('id','endpoint__slug','delivery_id','status','payload_sha256','received_at'),('endpoint__slug','delivery_id','status'))

@csrf_exempt
@endpoint('POST',public=True)
def ingress(request,slug):
    try:rate_limit(request,'webhook')
    except Throttled:raise Problem('rate_limited',429) from None
    cap=512*1024
    try:
        if int(request.META.get('CONTENT_LENGTH') or 0)>cap:raise Problem('payload_too_large',413)
    except ValueError:raise Problem('invalid_length')
    raw=request.read(cap+1)
    if len(raw)>cap:raise Problem('payload_too_large',413)
    digest=hashlib.sha256(raw).hexdigest()
    with transaction.atomic():
        r=WebhookEndpoint.objects.select_for_update().filter(slug=slug,active=True).first()
        if not r:raise Problem('not_found',404)
        def reject(reason):
            WebhookDelivery.objects.create(endpoint=r,delivery_id='rejected-'+secrets.token_hex(20),status='rejected',payload_sha256=digest,payload=b'')
            record('webhook.rejected',endpoint_id=r.pk,reason=reason)
            return JsonResponse({'error':reason},status=401)
        signed=raw
        if r.timestamp_header:
            t=request.headers.get(r.timestamp_header,'')
            if not t.isascii() or not t.isdecimal() or len(t)>12 or abs(time.time()-int(t))>r.tolerance_seconds:return reject('invalid_timestamp')
            signed=t.encode()+b'.'+raw
        sig=request.headers.get(r.signature_header,'').removeprefix('sha256=')
        if not sig.isascii() or not hmac.compare_digest(sig,hmac.digest(cipher().decrypt(r.encrypted_secret.encode()),signed,'sha256').hex()):return reject('invalid_signature')
        ident=request.headers.get('X-Webhook-Id') or request.headers.get('X-Event-Id') or digest
        if len(ident)>128:raise Problem('invalid_delivery_id')
        delivery,created=WebhookDelivery.objects.get_or_create(endpoint=r,delivery_id=ident,defaults=dict(payload=raw,payload_sha256=digest))
        if created:enqueue(module='webhook',event=slug+'.received',object_id=delivery.pk,key=f'webhook:{delivery.pk}',recipients=[])
        status='accepted' if created else 'duplicate'
        record('webhook.'+status,delivery_id=delivery.pk)
        return {'status':status}
