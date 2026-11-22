import base64,hashlib,http.client,ipaddress,json,re,socket,ssl,time
from urllib.parse import urlsplit,urlencode
from cryptography.fernet import Fernet
from django.conf import settings
from backbone.models import ApiConnector,OutboundApiLog
from backbone.validation import Problem
from backbone.correlation import record

def cipher(key=None):
    key=settings.ERP_CONNECTOR_KEY if key is None else key
    if len(key)<32 or key in (settings.SECRET_KEY,settings.ERP_MFA_KEY,settings.ERP_CUSTOMER_JWT_KEY):
        raise Problem('connector_key_required',503)
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b'erpi/connector/v1\0'+key.encode()).digest()))

def resolve(base):
    try:
        u=urlsplit(base)
        if u.scheme not in (('http','https') if settings.DEBUG else ('https',)) or not u.hostname or u.username is not None or u.fragment or u.query or '\\' in base:raise ValueError()
        port=u.port or (443 if u.scheme=='https' else 80)
        ips=[ipaddress.ip_address(r[4][0]) for r in socket.getaddrinfo(u.hostname,port,type=socket.SOCK_STREAM)]
        if not ips or not settings.DEBUG and any(not ip.is_global or ip.is_multicast or ip.is_reserved for ip in ips):raise ValueError()
        return u,port,str(ips[0])
    except (TypeError,ValueError,OSError):raise Problem('invalid_destination',field='base_url') from None

def path_check(path):
    if not isinstance(path,str) or not path.startswith('/') or path.startswith('//') or any(c in path for c in '?#\\') or not path.isascii() or any(ord(c)<33 for c in path):raise Problem('invalid_path',field='health_path')

def header_check(k):
    if not isinstance(k,str) or not re.fullmatch('[A-Za-z][A-Za-z0-9-]{0,79}',k) or k.lower() in {'host','connection','content-length','transfer-encoding','cookie','upgrade','proxy-authorization'}:raise Problem('invalid_header')

def send(c,method,path,headers,body):
    u,port,ip=resolve(c.base_url)
    conn=http.client.HTTPConnection(u.hostname,port,timeout=c.timeout_seconds)
    try:
        conn.sock=socket.create_connection((ip,port),timeout=c.timeout_seconds)
        if u.scheme=='https':conn.sock=ssl.create_default_context().wrap_socket(conn.sock,server_hostname=u.hostname)
        conn.request(method,u.path.rstrip('/')+path,headers=headers,body=body)
        response=conn.getresponse()
        data=response.read(2097153)
        if len(data)>2097152:raise Problem('response_too_large',502)
        return response.status,data
    finally:conn.close()

# Only code-declared route shapes may enter operational logs.
LOG_ROUTES = (("/drug/label.json", re.compile(r"/drug/label\.json")), ("/invoices/{invoice_id}",re.compile(r"/invoices/[^/?#]+")))
def log_path(path):
    for template, pattern in LOG_ROUTES:
        if pattern.fullmatch(path):return template
    return "/[resource]"

def call_connector(slug,method,path,*,params=None,json_body=None,headers=None,idempotency_key=None,site_id=None):
    c=ApiConnector.objects.filter(slug=slug,active=True).first()
    if not c or c.site_id not in (None,site_id):raise Problem('not_found',404)
    method=method.upper()
    path_check(path)
    if method not in ('GET','HEAD','POST','PATCH','PUT','DELETE'):raise Problem('invalid_method')
    h=dict(headers or {})
    for k in h:
        header_check(k)
        if k.lower() in ('authorization',c.auth_header_name.lower()):raise Problem('reserved_header')
    if c.auth_type!='none':
        value=cipher().decrypt(c.encrypted_credentials.encode()).decode()
        if c.auth_type=='basic':value='Basic '+base64.b64encode(value.encode()).decode()
        elif c.auth_type=='bearer':value='Bearer '+value
        h[c.auth_header_name if c.auth_type=='api_key_header' else 'Authorization']=value
    if idempotency_key:h['Idempotency-Key']=idempotency_key
    body=None
    if json_body is not None:
        h['Content-Type']='application/json'
        body=json.dumps(json_body).encode()
    target=path+('?'+urlencode(params,doseq=True) if params else '')
    attempts=c.max_retries+1 if method in ('GET','HEAD','PUT','DELETE') or idempotency_key else 1
    start=time.monotonic()
    status=None
    error=''
    try:
        for i in range(attempts):
            try:
                status,data=send(c,method,target,h,body)
                if status not in (429,502,503,504) or i+1==attempts:return dict(status=status,body=data,duration_ms=round((time.monotonic()-start)*1000))
            except (OSError,http.client.HTTPException):
                status=None
                if i+1==attempts:raise Problem('upstream_unavailable',502) from None
            time.sleep(min(2,.2*2**i))
    except Problem as exc:
        error=exc.code
        raise
    finally:
        ok=bool(status and 200<=status<300 and not error)
        OutboundApiLog.objects.create(connector=c,method=method,path=log_path(path),status_code=status,duration_ms=round((time.monotonic()-start)*1000),success=ok,error_code=error or ('' if ok else 'upstream_http'))
        record('connector.call',connector_id=c.pk,status=status)
