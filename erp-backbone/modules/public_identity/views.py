from django.conf import settings
from django.db import transaction
from django.http import HttpResponse,JsonResponse
from django.middleware.csrf import get_token
from rest_framework.views import APIView,exception_handler
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from backbone.registry import enabled_modules
from . import services as s
from .security import CustomerAuthentication,enforce_csrf,rate_limit

COOKIE="erpi_customer_refresh"
COOKIE_PATH="/api/public/v1/auth/"

def errors(exc,context):
    response=exception_handler(exc,context)
    if response is not None:
        payload=response.data
        if isinstance(payload,dict) and "detail" not in payload:
            response.data={"error":"validation_failed","field_errors":payload,"field_error_codes":{key:[str(getattr(x,"code","invalid")) for x in (values if isinstance(values,list) else [values])] for key,values in payload.items()}}
        else: response.data={"error":"request_failed","detail":payload.get("detail") if isinstance(payload,dict) else payload}
    return response

class PublicView(APIView):
    authentication_classes=[]
    permission_classes=[]
    def finalize_response(self,request,response,*args,**kwargs):
        response=super().finalize_response(request,response,*args,**kwargs)
        response["Cache-Control"]="no-store"
        return response
    def get_exception_handler(self): return errors
    def get_authenticate_header(self,request): return "Bearer"
    def initial(self,request,*args,**kwargs):
        super().initial(request,*args,**kwargs)
        from rest_framework.exceptions import NotFound
        if not {"publication","public_identity"}.issubset(enabled_modules()): raise NotFound()
        if request.method not in {"GET","HEAD","OPTIONS"}:
            enforce_csrf(request)
            if not isinstance(request.data,dict):
                from rest_framework.exceptions import ValidationError
                raise ValidationError({"non_field_errors":["Expected a JSON object."]})

class CSRF(PublicView):
    def get(self,request): return Response({"csrf_token":get_token(request._request)})

class Auth(PublicView):
    def post(self,request,action):
        address=str(request.data.get("email",""))[:254].casefold()
        rate_limit(request,action,address)
        if action=="register": s.register(request.data);return Response({"status":"check_email"},status=202)
        if action in {"verify-email","password-reset-confirm"}:
            s.consume(request.data,"verify" if action=="verify-email" else "reset");return Response({"status":"completed"})
        if action in {"resend-verification","password-reset-request"}:
            s.request_challenge(request.data,"verify" if action=="resend-verification" else "reset")
            return Response({"status":"check_email"},status=202)
        if action in {"login","refresh"}:
            payload,refresh=s.login(request.data) if action=="login" else s.rotate(request.COOKIES.get(COOKIE,""))
            response=Response(payload)
            response.set_cookie(COOKIE,refresh,max_age=7*24*3600,secure=not settings.DEBUG,httponly=True,samesite="Strict",path=COOKIE_PATH)
            return response
        from rest_framework.exceptions import NotFound
        raise NotFound()

class CustomerView(APIView):
    authentication_classes=[CustomerAuthentication]
    permission_classes=[IsAuthenticated]
    def finalize_response(self,request,response,*args,**kwargs):
        response=super().finalize_response(request,response,*args,**kwargs)
        response["Cache-Control"]="no-store"
        return response
    def get_exception_handler(self): return errors

class Me(CustomerView):
    def get(self,request):
        identity=request.user.identity
        return Response({"id":str(identity.pk),"email":identity.normalized_email,"display_name":identity.display_name,
            "space_id":request.auth.space_id,"revision":identity.revision})
    def patch(self,request):
        from rest_framework.exceptions import APIException
        with transaction.atomic():
            identity=s.ExternalIdentity.objects.select_for_update().get(pk=request.user.pk)
            if request.data.get("revision")!=identity.revision:
                return Response({"error":"revision_conflict"},status=409)
            identity.display_name=s.text(request.data,"display_name");identity.revision+=1;identity.save()
            s.event(identity,"profile_updated")
        request.user.identity.refresh_from_db()
        return self.get(request)

class Logout(CustomerView):
    def post(self,request,all_sessions=False):
        s.logout(request.auth,all_sessions)
        response=Response({"status":"signed_out"})
        response.delete_cookie(COOKIE,path=COOKIE_PATH,samesite="Strict")
        return response

# Portal presentation is isolated from API authentication and payload contracts.
def _portal_document(template, nonce, **values):
    from pathlib import Path
    root = Path(__file__).parent
    html = (root / template).read_text()
    html = html.replace("PORTAL_THEME", (root / "portal_ui" / "theme.css").read_text())
    script = "account.js" if template == "account.html" else "confirm.js"
    html = html.replace("PORTAL_SCRIPT", (root / "portal_ui" / script).read_text())
    for key, value in values.items():
        html = html.replace(key, str(value))
    return html.replace("NONCE", nonce)


def _portal_response(template, *, status=200, **values):
    import secrets
    nonce = secrets.token_urlsafe(24)
    response = HttpResponse(_portal_document(template, nonce, **values), status=status)
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "same-origin"
    response["Content-Security-Policy"] = (f"default-src 'none'; script-src 'nonce-{nonce}'; "
        f"style-src 'nonce-{nonce}'; connect-src 'self'; img-src 'self' data: blob:; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'")
    return response


# The capability stays in the URL fragment: browsers do not send it in access logs.
def confirm_page(request):
    from django.utils.html import escape
    from rest_framework.exceptions import ValidationError
    if not {"publication", "public_identity"}.issubset(enabled_modules()):
        return HttpResponse(status=404)
    if request.method not in {"GET", "POST"}:
        return HttpResponse(status=405)
    if request.method == "POST":
        from .security import CSRFCheck
        check = CSRFCheck(lambda r: None)
        check.process_request(request)
        if check.process_view(request, lambda r: None, (), {}):
            return HttpResponse("CSRF verification failed", status=403)
        purpose = request.POST.get("purpose")
        if purpose not in {"verify", "reset"}:
            return HttpResponse(status=400)
        try:
            s.consume(request.POST, purpose)
        except ValidationError:
            return _portal_response("confirm.html", status=400, PORTAL_RESULT="invalid", PORTAL_CSRF="")
        return _portal_response("confirm.html", PORTAL_RESULT="completed", PORTAL_CSRF="")
    return _portal_response("confirm.html", PORTAL_RESULT="", PORTAL_CSRF=escape(get_token(request)))


def account_page(request):
    if not {"publication", "public_identity"}.issubset(enabled_modules()):
        return HttpResponse(status=404)
    return _portal_response("account.html")
