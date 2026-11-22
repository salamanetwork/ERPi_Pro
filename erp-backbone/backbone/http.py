import hashlib
import json
from functools import wraps
from django.db import transaction
from django.http import HttpResponseBase, JsonResponse
from .access import has_permission
from .mfa import session_allowed
from .models import CreateReceipt
from .registry import enabled_modules
from .validation import Problem


def csrf_failure(request, reason=""):
    return JsonResponse({"error": "csrf_failed"}, status=403)


def endpoint(*methods, public=False, admin=False, module=None):
    def decorate(view):
        @wraps(view)
        def handle(request, *args, **kwargs):
            try:
                if request.method not in methods:
                    raise Problem("method_not_allowed", 405)
                if not public and (not request.user.is_authenticated or not session_allowed(request)):
                    raise Problem("login_required", 401)
                if admin and not request.user.is_superuser:
                    raise Problem("forbidden", 403)
                if module and module not in enabled_modules():
                    raise Problem("module_disabled", 404)
                result = view(request, *args, **kwargs)
                if isinstance(result, HttpResponseBase):
                    return result
                if isinstance(result, tuple):
                    return JsonResponse(result[0], status=result[1])
                return JsonResponse(result)
            except Problem as error:
                return JsonResponse(error.response(), status=error.status)
        return handle
    return decorate


def body(request):
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeError):
        raise Problem("invalid_json") from None
    if not isinstance(data, dict):
        raise Problem("invalid_json")
    return data


def string(data, field, limit, *, required=True):
    value = data.get(field, "")
    if value is None or isinstance(value, str) and not value.strip():
        if required:
            raise Problem(f"invalid_{field}", field=field, reason="required")
        if value is None:
            raise Problem(f"invalid_{field}", field=field)
    if not isinstance(value, str):
        raise Problem(f"invalid_{field}", field=field)
    if len(value.strip()) > limit:
        raise Problem(f"invalid_{field}", field=field, reason="max_length")
    return value.strip()


def identifier(value, field, *, optional=False):
    if optional and value is None:
        return None
    if type(value) is not int or not 0 < value < 2**63:
        raise Problem(f"invalid_{field}", field=field,
                      reason="required" if value is None else "invalid_choice")
    return value


def site(data):
    value = identifier(data.get("site_id"), "site", optional=True)
    from .site_admin import require_active_site

    try:
        require_active_site(value, lock=transaction.get_connection().in_atomic_block)
    except Problem as error:
        if error.code != "invalid_site":
            raise
        raise Problem(error.code, field="site_id", reason="invalid_choice") from None
    return value


def require(user, permission, scope=...):
    if not has_permission(user, permission, scope):
        raise Problem("forbidden", 403)


def once(request, operation, payload, create):
    key = request.headers.get("Idempotency-Key", "").strip()
    if not key or len(key) > 120:
        raise Problem("invalid_idempotency_key")
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    with transaction.atomic():
        receipt, fresh = CreateReceipt.objects.get_or_create(
            user=request.user, operation=operation, key=key,
            defaults={"fingerprint": fingerprint},
        )
        if not fresh:
            if receipt.fingerprint != fingerprint:
                raise Problem("idempotency_conflict", 409)
            return receipt.response, 200
        receipt.response = create()
        receipt.save(update_fields=["response"])
        return receipt.response, 201
