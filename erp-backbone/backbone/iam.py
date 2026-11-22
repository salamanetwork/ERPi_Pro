"""Staff administration. Legacy assignment endpoints retain their original contracts."""
from django.conf import settings
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Prefetch, Q, Value
from django.db.models.functions import Concat
from django.views.decorators.cache import never_cache
from .access import audit
from .http import Problem, body, endpoint, identifier, site, string
from .models import Assignment, Profile, Role
from .registry import PERMISSIONS
from .views import display_name

User = get_user_model()


def admin_endpoint(*methods):
    def decorate(view):
        @never_cache
        @endpoint(*methods, admin=True)
        def handle(request, *args, **kwargs):
            if request.headers.get("Authorization"):
                raise Problem("forbidden", 403)
            return view(request, *args, **kwargs)
        return handle
    return decorate


def expect_revision(data, current):
    if type(data.get("revision")) is not int or data["revision"] != current:
        raise Problem("revision_conflict", 409)


def password(data, user):
    value = data.get("password")
    if not isinstance(value, str) or len(value) > 256:
        raise Problem("invalid_password", field="password")
    try:
        validate_password(value, user)
    except ValidationError:
        raise Problem("invalid_password", field="password") from None
    return value


def serialize(user):
    profile = getattr(user, "profile", None) or Profile(iam_revision=1, iam_display_name="")
    return {"id": user.pk, "username": user.username, "display_name": display_name(user, profile),
            "active": user.is_active, "admin": user.is_superuser, "revision": profile.iam_revision,
            "assignments": [{"id": a.pk, "role_id": a.role_id, "role": a.role.name,
                             "site_id": a.site_id, "site": a.site.name if a.site_id else None}
                            for a in user.assignment_set.all()]}


def directory():
    grants = Assignment.objects.select_related("role", "site").order_by("role__name", "site__name")
    return User.objects.select_related("profile").prefetch_related(Prefetch("assignment_set", queryset=grants))


@admin_endpoint("GET", "POST")
def users(request):
    if request.method == "POST":
        return create_user(request)
    rows = directory().order_by("username", "pk")
    q = request.GET.get("q", "").strip()[:150]
    if q:
        rows = rows.annotate(full=Concat("first_name", Value(" "), "profile__middle_name", Value(" "), "last_name"),
                             short=Concat("first_name", Value(" "), "last_name")).filter(
            Q(username__icontains=q) | Q(profile__iam_display_name__icontains=q) |
            Q(full__icontains=q) | Q(short__icontains=q))
    status = request.GET.get("status", "all")
    filters = {"all": {}, "active": {"is_active": True}, "inactive": {"is_active": False}, "admins": {"is_superuser": True}}
    if status not in filters:
        raise Problem("invalid_status")
    size=request.GET.get('page_size','25')
    if size not in ('10','25','50'): raise Problem('invalid_page_size')
    page = Paginator(rows.filter(**filters[status]), int(size)).get_page(request.GET.get("page", 1))
    return {"count": page.paginator.count, "page": page.number, "pages": page.paginator.num_pages,
            "results": [serialize(user) for user in page]}


def create_user(request):
    data = body(request)
    user = User(username=string(data, "username", 150))
    name = string(data, "display_name", 150)
    user.set_password(password(data, user))
    try:
        user.full_clean(validate_unique=False)
        with transaction.atomic():
            user.save()
            Profile.objects.create(user=user, iam_display_name=name)
            audit(request.user, "user.create", user.pk)
            if data.get("role_id") is not None:
                role = Role.objects.filter(pk=identifier(data["role_id"], "role")).first()
                if role is None:
                    raise Problem("invalid_assignment", field="role_id")
                grant = Assignment.objects.create(user=user, role=role, site_id=site(data))
                audit(request.user, "assignment.grant", grant.pk)
            elif data.get("site_id") is not None:
                raise Problem("invalid_assignment", field="role_id")
    except ValidationError:
        raise Problem("invalid_account", field="username") from None
    except IntegrityError:
        raise Problem("duplicate_name", 409, field="username", reason="duplicate") from None
    return {"id": user.pk}, 201


def lock_user(pk, data):
    user = User.objects.select_for_update().filter(pk=pk).first()
    if user is None:
        raise Problem("not_found", 404)
    profile, _ = Profile.objects.select_for_update().get_or_create(
        user=user, defaults={"iam_revision": 1}
    )
    expect_revision(data, profile.iam_revision)
    return user, profile


@admin_endpoint("GET", "PATCH")
def user_detail(request, pk):
    if request.method == "PATCH":
        data = body(request)
        with transaction.atomic():
            if data.get("active") is False:
                # Two administrators cannot deactivate one another concurrently.
                admins = list(User.objects.select_for_update().filter(is_superuser=True)
                             .order_by("pk").values_list("pk", "is_active"))
                if (request.user.pk, True) not in admins:
                    raise Problem("forbidden", 403)
            user, profile = lock_user(pk, data)
            if "active" in data:
                if type(data["active"]) is not bool:
                    raise Problem("invalid_active", field="active")
                if not data["active"] and user.pk == request.user.pk:
                    raise Problem("self_deactivation", 409)
                user.is_active = data["active"]
                user.save(update_fields=["is_active"])
            if "display_name" in data:
                profile.iam_display_name = string(data, "display_name", 150)
            profile.iam_revision += 1
            profile.save(update_fields=["iam_display_name", "iam_revision"])
            audit(request.user, "user.update", user.pk)
    user = directory().filter(pk=pk).first()
    if user is None:
        raise Problem("not_found", 404)
    return serialize(user)


@admin_endpoint("POST")
def reset_password(request, pk):
    data = body(request)
    with transaction.atomic():
        user, profile = lock_user(pk, data)
        user.set_password(password(data, user))
        user.save(update_fields=["password"])
        profile.iam_revision += 1
        profile.save(update_fields=["iam_revision"])
        audit(request.user, "user.password_reset", user.pk)
    if user.pk == request.user.pk:
        update_session_auth_hash(request, user)
    return {"revision": profile.iam_revision}


def role_fields(data):
    name = string(data, "name", 120)
    permissions = data.get("permissions")
    if not isinstance(permissions, list) or not permissions or any(
        not isinstance(key, str) or key not in PERMISSIONS for key in permissions
    ):
        raise Problem("invalid_permissions", field="permissions")
    return name, sorted(set(permissions))


@admin_endpoint("GET", "POST")
def roles(request):
    if request.method == "GET":
        rows = Role.objects.annotate(user_count=Count("assignment__user", distinct=True)).order_by("name")
        return {"results": list(rows.values("id", "name", "revision", "permissions", "user_count"))}
    name, permissions = role_fields(body(request))
    try:
        with transaction.atomic():
            role = Role.objects.create(name=name, permissions=permissions)
            audit(request.user, "role.create", role.pk)
    except IntegrityError:
        raise Problem("duplicate_name", 409) from None
    return {"id": role.pk}, 201


@admin_endpoint("PATCH")
def role_detail(request, pk):
    data = body(request)
    name, permissions = role_fields(data)
    try:
        with transaction.atomic():
            role = Role.objects.select_for_update().filter(pk=pk).first()
            if role is None:
                raise Problem("not_found", 404)
            expect_revision(data, role.revision)
            if role.name != name and role.name in settings.ERP_MFA_ROLES:
                raise Problem("mfa_role_name", 409)
            role.name, role.permissions = name, permissions
            role.revision += 1
            role.save(update_fields=["name", "permissions", "revision"])
            audit(request.user, "role.update", role.pk)
    except IntegrityError:
        raise Problem("duplicate_name", 409) from None
    return {"id": role.pk, "revision": role.revision}
