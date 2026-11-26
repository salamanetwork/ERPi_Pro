import json as _json
from pathlib import Path as _Path

def _load_locale_registry():
    manifest_path = _Path(__file__).resolve().parent.parent / "locales.json"
    if manifest_path.exists():
        data = _json.loads(manifest_path.read_text(encoding="utf-8"))
        return {item["code"]: item for item in data.get("locales", [])}
    return {"en": {"code": "en", "dir": "ltr"}, "ar": {"code": "ar", "dir": "rtl"}}

from .extensions import module_can_disable
from django.conf import settings
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import FileResponse, Http404, HttpResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.cache import never_cache

from . import mfa, navigation
from .profile_picture import picture_url
from .access import audit, has_permission, preload_scopes, scopes
from .http import Problem, body, endpoint, identifier, require, site, string
from .models import Assignment, AuditEvent, Authenticator, ModuleState, NavigationSettings, Profile, Role, Site
from .compatibility import compatibility_issue, effective_modules
from .registry import MANIFESTS, PERMISSION_METADATA, PERMISSIONS, PLATFORM_VERSION, RELEASE_VERSION, enabled_modules, modules_for


@never_cache
@ensure_csrf_cookie
def shell(request):
    index = settings.BASE_DIR / "frontend/dist/index.html"
    if not index.exists():
        return HttpResponse("Build the frontend first. See README.md.", status=503)
    return FileResponse(index.open("rb"), content_type="text/html")


@never_cache
@ensure_csrf_cookie
@endpoint("GET", public=True)
def session(request):
    if not request.user.is_authenticated:
        challenge = mfa.describe(request.session.get(mfa.CHALLENGE))
        return {"user": None, "mfa": challenge} if challenge else {"user": None}
    if not mfa.session_allowed(request):
        logout(request)
        return {"user": None}
    return {"user": {"username": request.user.get_username(), "admin": request.user.is_superuser}}


@never_cache
@endpoint("POST", public=True)
def sign_in(request):
    data = body(request)
    username = string(data, "username", 150)
    password = data.get("password")
    if not isinstance(password, str) or not 1 <= len(password) <= 256:
        raise Problem("invalid_credentials", 401)
    user = authenticate(request, username=username, password=password)
    if user is None:
        raise Problem("invalid_credentials", 401)
    if mfa.required(user) or Authenticator.objects.filter(user=user, encrypted_key__gt="").exists():
        challenge = mfa.start(request, user)
        if not challenge:
            raise Problem("mfa_locked", 429)
        return {"ok": False, "mfa": challenge}
    request.session.flush()
    login(request, user)
    return {"ok": True}


@never_cache
@endpoint("POST", public=True)
def verify_mfa(request):
    code = string(body(request), "code", 6)
    if not (len(code) == 6 and code.isascii() and code.isdigit()):
        raise Problem("invalid_mfa_code")
    error = mfa.verify(request, code)
    if error:
        raise Problem(error, 429 if error == "mfa_locked" else 400)
    return {"ok": True}


@never_cache
@endpoint("POST", public=True)
def sign_out(request):
    logout(request)
    return {"ok": True}


@endpoint("GET")
def workspace(request):
    from .company_profile import branding

    user = request.user
    preload_scopes(user)
    profile, _ = Profile.objects.get_or_create(user=user)
    permissions = {key: sorted(scopes(user, key), key=lambda value: value or 0) for key in sorted(PERMISSIONS)}
    accessible = {value for values in permissions.values() for value in values}
    sites = Site.objects.order_by("name")
    if None not in accessible:
        sites = sites.filter(id__in=accessible)
    return {
        "username": user.get_username(), "admin": user.is_superuser, "platform_version": RELEASE_VERSION, "mode": settings.ERP_APP_MODE,
        "language": profile.language, "theme": profile.theme,
        "system_notice_mode": NavigationSettings.objects.filter(pk=1).values_list("system_notice_mode", flat=True).first() or "popup",
        "notice_mode_override": profile.notice_mode_override or "system",
        "display_name": display_name(user, profile), "module_view": profile.module_view,
        "picture_url": picture_url(user.pk), "company": branding(), "hidden_screens": navigation.visibility(user),
        "modules": modules_for(user), "sites": list(sites.values("id", "name", "active")), "permissions": permissions,
    }


def display_name(user, profile):
    if profile.iam_display_name:
        return profile.iam_display_name
    if not user.first_name or not user.last_name:
        return user.get_username()
    return " ".join(part for part in (user.first_name, profile.middle_name, user.last_name) if part)


@never_cache
@endpoint("GET", "PATCH")
def user_profile(request):
    from hr.models import Employee

    user = request.user
    profile, _ = Profile.objects.get_or_create(user=user)
    if request.method == "PATCH":
        data = body(request)
        allowed_keys = {"first_name", "middle_name", "last_name", "notice_mode_override"}
        if not set(data).issubset(allowed_keys) or not {"first_name", "middle_name", "last_name"}.issubset(set(data)):
            raise Problem("invalid_profile")
        fields = {key: string(data, key, 150, required=False)
                  for key in ("first_name", "middle_name", "last_name")}
        override = string(data, "notice_mode_override", 10, required=False) or profile.notice_mode_override or "system"
        if override not in {"system", "popup", "toast"}:
            raise Problem("invalid_profile")
        with transaction.atomic():
            user.first_name = fields["first_name"]
            user.last_name = fields["last_name"]
            user.save(update_fields=["first_name", "last_name"])
            profile.middle_name = fields["middle_name"]
            profile.notice_mode_override = override
            from django.db.models import F
            profile.iam_display_name = ""
            profile.iam_revision = F("iam_revision") + 1
            profile.save(update_fields=["middle_name", "notice_mode_override", "iam_display_name", "iam_revision"])
            audit(user, "profile.update", user.pk)

    number = employee_id = None
    if "hr" in enabled_modules():
        employee = Employee.objects.filter(user=user).values("id", "number", "site_id").first()
        if employee and (has_permission(user, "hr.self", employee["site_id"]) or
                         has_permission(user, "hr.view", employee["site_id"]) or
                         has_permission(user, "hr.manage", employee["site_id"]) or
                         has_permission(user, "hr.identity.view_full", employee["site_id"])):
            number, employee_id = employee["number"], employee["id"]
    return {"username": user.get_username(), "first_name": user.first_name, "middle_name": profile.middle_name,
            "last_name": user.last_name, "display_name": display_name(user, profile), "employee_number": number,
            "employee_id": employee_id, "picture_url": picture_url(user.pk),
            "notice_mode_override": profile.notice_mode_override or "system"}


@never_cache
@endpoint("GET", "PATCH", admin=True)
def navigation_settings(request):
    if request.method == "GET":
        return {"hidden_screens": navigation.visibility(request.user)}
    hidden = body(request).get("hidden_screens")
    if not isinstance(hidden, list) or any(not isinstance(key, str) or key not in navigation.SCREEN_ID_SET for key in hidden):
        raise Problem("invalid_module_visibility")
    with transaction.atomic():
        hidden = sorted(set(hidden))
        NavigationSettings.objects.update_or_create(pk=1, defaults={"hidden_screens": hidden})
        audit(request.user, "navigation.visibility", "installation")
    return {"hidden_screens": hidden}


@endpoint("PATCH", admin=True)
def module_view(request):
    choice = body(request).get("module_view")
    if choice not in ("list", "grid", "card"):
        raise Problem("invalid_preferences")
    Profile.objects.update_or_create(user=request.user, defaults={"module_view": choice})
    return {"module_view": choice}


@endpoint("PATCH")
def preferences(request):
    data = body(request)
    profile, _ = Profile.objects.get_or_create(user=request.user)
    language = string(data, "language", 2, required=False) or profile.language
    theme = string(data, "theme", 8, required=False) or profile.theme
    if language not in _load_locale_registry() or theme not in {"light", "dark", "system"}:
        raise Problem("invalid_preferences")
    defaults = {"language": language, "theme": theme}
    if "notice_mode_override" in data:
        override = string(data, "notice_mode_override", 10, required=False) or "system"
        if override not in {"system", "popup", "toast"}:
            raise Problem("invalid_preferences")
        defaults["notice_mode_override"] = override
    Profile.objects.update_or_create(user=request.user, defaults=defaults)
    sys_mode = NavigationSettings.objects.filter(pk=1).values_list("system_notice_mode", flat=True).first() or "popup"
    if "system_notice_mode" in data:
        if not request.user.is_superuser:
            raise Problem("forbidden", 403)
        req_sys = string(data, "system_notice_mode", 10, required=False) or "popup"
        if req_sys not in {"popup", "toast"}:
            raise Problem("invalid_preferences")
        NavigationSettings.objects.update_or_create(pk=1, defaults={"system_notice_mode": req_sys})
        sys_mode = req_sys
    return {
        "language": language,
        "theme": theme,
        "system_notice_mode": sys_mode,
        "notice_mode_override": defaults.get("notice_mode_override", profile.notice_mode_override or "system"),
    }


@endpoint("GET",admin=True)
def module_directory(request):
    from .services.pagination import paginate
    term=string(request.GET,"q",150,required=False).casefold()
    rows=sorted(MANIFESTS.values(),key=lambda m:(m.get("group",""),m["id"]))
    ids=[m["id"] for m in rows if not term or any(term in str(value).casefold() for value in
         (m["id"],m.get("category"),m.get("group"),*m["name"].values()))]
    return paginate(request,ids,lambda key:key)

@endpoint("PATCH", admin=True)
def module_state(request, module_id):
    manifest = MANIFESTS.get(module_id)
    if manifest is None:
        raise Problem("not_found", 404)
    desired = body(request).get("enabled")
    if desired is False and manifest.get("category") == "platform_core":
        raise Problem("required_by_module", 409)
    if type(desired) is not bool:
        raise Problem("invalid_enabled")
    if manifest.get("category") == "platform_core":
        return {"modules": modules_for(request.user)}
    with transaction.atomic():
        for key in sorted(MANIFESTS):
            if MANIFESTS[key].get("category") != "platform_core":
                ModuleState.objects.get_or_create(module_id=key)
        states = {row.module_id: row for row in ModuleState.objects.select_for_update().order_by("module_id")}
        active = {key for key, row in states.items() if row.enabled}
        if desired:
            if compatibility_issue(manifest):
                raise Problem("incompatible_module", 409)
            if set(manifest["required_dependencies"]) - effective_modules(MANIFESTS, active):
                raise Problem("missing_dependency", 409)
        elif any(module_id in MANIFESTS[key]["required_dependencies"] for key in active if key in MANIFESTS):
            raise Problem("required_by_module", 409)
        state = states[module_id]
        if state.enabled and not desired and not module_can_disable(module_id):
            raise Problem("module_has_open_records", 409)
        if state.enabled != desired:
            state.enabled = desired
            state.save(update_fields=["enabled"])
            audit(request.user, "module.enable" if desired else "module.disable", module_id)
    return {"modules": modules_for(request.user)}


@endpoint("GET", admin=True)
def access_settings(request):
    return {
        "roles": list(Role.objects.order_by("name").values("id", "name", "permissions", "hidden_modules")),
        "users": list(get_user_model().objects.order_by("username").values("id", "username")),
        "sites": list(Site.objects.filter(active=True).order_by("name").values("id", "name", "active")),
        "assignments": list(Assignment.objects.order_by("id").values(
            "id", "user_id", "role_id", "site_id", "user__username", "role__name", "site__name")),
        "permission_catalog": sorted(PERMISSIONS),
        "permission_metadata": PERMISSION_METADATA,
    }


@endpoint("POST", admin=True)
def add_site(request):
    data = body(request)
    name = string(data, "name", 120)
    description = string(data, "description", 500, required=False)
    active = data.get("active", True)
    if type(active) is not bool:
        raise Problem("invalid_site", field="active")
    with transaction.atomic():
        obj, fresh = Site.objects.get_or_create(name=name, defaults={"active":active, "description":description})
        if not fresh:
            raise Problem("duplicate_name", 409)
        audit(request.user, "site.create", obj.pk)
    return {"id": obj.pk}, 201


@endpoint("POST", admin=True)
def add_user(request):
    data = body(request)
    username = string(data, "username", 150)
    password = data.get("password")
    model = get_user_model()
    user = model(username=username)
    if not isinstance(password, str) or not 1 <= len(password) <= 256:
        raise Problem("invalid_password")
    try:
        user.full_clean(exclude=["password"], validate_unique=False)
        validate_password(password, user)
    except ValidationError:
        raise Problem("invalid_account") from None
    try:
        with transaction.atomic():
            user.set_password(password)
            user.save()
            audit(request.user, "user.create", user.pk)
    except IntegrityError:
        raise Problem("duplicate_name", 409) from None
    return {"id": user.pk}, 201


@endpoint("POST", admin=True)
def add_role(request):
    data = body(request)
    name = string(data, "name", 120)
    permissions = data.get("permissions")
    if not isinstance(permissions, list) or not permissions or any(
        not isinstance(item, str) or item not in PERMISSIONS for item in permissions
    ):
        raise Problem("invalid_permissions")
    with transaction.atomic():
        obj, fresh = Role.objects.get_or_create(name=name, defaults={"permissions": sorted(set(permissions))})
        if not fresh:
            raise Problem("duplicate_name", 409)
        audit(request.user, "role.create", obj.pk)
    return {"id": obj.pk}, 201


@endpoint("GET","POST", admin=True)
def add_assignment(request):
    if request.method=='GET':
        from .services.pagination import paginate,positive_filter
        rows=Assignment.objects.all()
        params=request.GET
        if uid:=positive_filter(params,'user_id'):rows=rows.filter(user_id=uid)
        if params.get('user'):rows=rows.filter(user__username__icontains=params['user'][:150])
        if params.get('role'):rows=rows.filter(role__name__icontains=params['role'][:150])
        if params.get('site')=='all':rows=rows.filter(site=None)
        elif sid:=positive_filter(params,'site'):rows=rows.filter(site_id=sid)
        rows=rows.order_by('user__username','pk') if params.get('group')=='1' else rows.order_by('pk')
        return paginate(request,rows.values('id','user_id','role_id','site_id','user__username','role__name','site__name'),lambda x:x)
    data = body(request)
    user = identifier(data.get("user_id"), "user")
    role = identifier(data.get("role_id"), "role")
    scope = site(data)
    if not get_user_model().objects.filter(pk=user, is_active=True).exists() or not Role.objects.filter(pk=role).exists():
        raise Problem("invalid_assignment")
    with transaction.atomic():
        from .site_admin import require_active_site

        require_active_site(scope, lock=True)
        row, fresh = Assignment.objects.get_or_create(user_id=user, role_id=role, site_id=scope)
        if not fresh:
            raise Problem("duplicate_assignment", 409)
        audit(request.user, "assignment.grant", row.pk)
    return {"id": row.pk}, 201


@endpoint("DELETE", admin=True)
def revoke_assignment(request, pk):
    with transaction.atomic():
        deleted, _ = Assignment.objects.filter(pk=pk).delete()
        if not deleted:
            raise Problem("not_found", 404)
        audit(request.user, "assignment.revoke", pk)
    return {"ok": True}


@endpoint("GET")
def audit_log(request):
    from .audit_views import filtered_events
    require(request.user, "platform.audit", None)
    from .business_scope import page
    rows = filtered_events(request.GET).values(
        "id", "actor", "actor_user_id", "action", "target", "occurred_at")
    paged=page(request.GET,rows) if any(k in request.GET for k in ("page","page_size")) else None
    events=paged["results"] if paged else list(rows[:200])
    ids = {event["actor_user_id"] for event in events if event["actor_user_id"] is not None}
    users = get_user_model().objects.in_bulk(ids)
    profiles = Profile.objects.in_bulk(ids, field_name="user")
    for event in events:
        key = event["actor_user_id"]
        event["display_name"] = display_name(users[key], profiles.get(key) or Profile(iam_display_name="")) if key in users else event["actor"]
    return {**(paged or {}), "events": events}

 
@endpoint("PATCH", admin=True)
def role_visibility(request, pk):
    hidden = body(request).get("hidden_modules")
    if not isinstance(hidden, list) or any(
        not isinstance(key, str) or (key not in MANIFESTS and key not in navigation.SCREEN_ID_SET) for key in hidden
    ):
        raise Problem("invalid_module_visibility")
    with transaction.atomic():
        role = Role.objects.select_for_update().filter(pk=pk).first()
        if role is None:
            raise Problem("not_found", 404)
        role.hidden_modules = sorted(set(hidden))
        role.save(update_fields=["hidden_modules"])
        audit(request.user, "role.module_visibility", pk)
    return {"id": role.pk, "hidden_modules": role.hidden_modules}
