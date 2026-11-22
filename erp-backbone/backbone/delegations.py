from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .access import DELEGABLE, DELEGATE_PERMISSION, audit, delegated_permissions, direct_scopes
from .http import Problem, body, endpoint, identifier, site, string
from .models import Assignment, Delegation, Site


def can_request(user, site_id):
    scopes = direct_scopes(user, DELEGATE_PERMISSION)
    return None in scopes or site_id in scopes


def serialize(grant, now):
    status = grant.status
    if status in (Delegation.Status.PENDING, Delegation.Status.APPROVED) and now >= grant.ends_at:
        status = "expired"
    elif status == Delegation.Status.APPROVED:
        if not grant.requester.is_active or not grant.delegate.is_active or not delegated_permissions(grant):
            status = "inactive"
        else:
            status = "scheduled" if now < grant.starts_at else "active"
    return {
        "id": grant.pk, "requester": grant.requester.get_username(), "delegate": grant.delegate.get_username(),
        "role": grant.role.name, "site": grant.site.name if grant.site_id else None, "permissions": grant.permissions,
        "starts_at": grant.starts_at, "ends_at": grant.ends_at, "reason": grant.reason,
        "status": status, "previous": grant.previous_id,
        "can_extend": grant.status == Delegation.Status.APPROVED and not grant.extensions.filter(
            status__in=[Delegation.Status.PENDING, Delegation.Status.APPROVED],
        ).exists(),
    }


def timestamp(data, field):
    try:
        value = parse_datetime(string(data, field, 40))
    except ValueError:
        value = None
    if not value or timezone.is_naive(value):
        raise Problem("invalid_delegation_window")
    return value


@endpoint("GET", "POST")
def delegations(request):
    user = request.user
    if request.method == "GET":
        result=directory(user)
        if "page_size" in request.GET:
            from .services.pagination import paginate_items
            result.update(paginate_items(request,result["items"],("requester","delegate","role","site","status")))
        return result
    data = body(request)
    reason = string(data, "reason", 500)
    ends_at = timestamp(data, "ends_at")
    previous_id = identifier(data.get("previous"), "previous", optional=True)

    try:
        with transaction.atomic():
            if previous_id is None:
                grant = new_request(user, data)
            else:
                previous = Delegation.objects.select_for_update().filter(
                    pk=previous_id, requester=user, status=Delegation.Status.APPROVED,
                ).first()
                if previous is None or ends_at <= max(timezone.now(), previous.ends_at):
                    raise Problem("invalid_extension")
                grant = Delegation(
                    requester=user, delegate=previous.delegate, source_assignment=previous.source_assignment,
                    role=previous.role, site_id=previous.site_id, permissions=previous.permissions,
                    starts_at=max(timezone.now(), previous.ends_at), previous=previous,
                )
            if not can_request(user, grant.site_id):
                raise Problem("forbidden", 403)
            if not grant.delegate.is_active or set(grant.permissions) != delegated_permissions(grant):
                raise Problem("invalid_delegation_permissions")
            if ends_at <= max(grant.starts_at, timezone.now()):
                raise Problem("invalid_delegation_window")
            grant.ends_at = ends_at
            grant.reason = reason
            grant.save()
            audit(user, "delegation.extend" if previous_id else "delegation.request", grant.pk)
    except IntegrityError as error:
        if getattr(getattr(error.__cause__, "diag", None), "constraint_name", None) == "delegation_one_current_extension":
            raise Problem("extension_exists", 409) from None
        raise
    return serialize(grant, timezone.now()), 201


def new_request(user, data):
    site_id = site(data)
    if not can_request(user, site_id):
        raise Problem("forbidden", 403)
    assignment_id = identifier(data.get("assignment_id"), "assignment")
    assignment = Assignment.objects.select_related("role").filter(pk=assignment_id, user=user).first()
    if assignment is None:
        raise Problem("invalid_delegation_permissions")
    recipient_id = identifier(data.get("delegate_id"), "delegate")
    recipient = get_user_model().objects.filter(pk=recipient_id, is_active=True).exclude(pk=user.pk).first()
    if recipient is None:
        raise Problem("invalid_delegate")
    permissions = data.get("permissions")
    if not isinstance(permissions, list) or not permissions or any(
        not isinstance(permission, str) or permission not in DELEGABLE for permission in permissions
    ):
        raise Problem("invalid_delegation_permissions")
    return Delegation(
        requester=user, delegate=recipient, source_assignment=assignment, role=assignment.role,
        site_id=site_id, permissions=sorted(set(permissions)), starts_at=timestamp(data, "starts_at"),
    )


def directory(user):
    now = timezone.now()
    rows = Delegation.objects.select_related("requester", "delegate", "role", "site", "source_assignment")
    if not user.is_superuser:
        rows = rows.filter(Q(requester=user) | Q(delegate=user))
    allowed = direct_scopes(user, DELEGATE_PERMISSION)
    sites = Site.objects.order_by("name")
    if None not in allowed:
        sites = sites.filter(pk__in=allowed)
    assignments = []
    if allowed:
        for row in Assignment.objects.filter(user=user).select_related("role").order_by("role__name", "id"):
            permissions = sorted(DELEGABLE.intersection(row.role.permissions))
            if permissions and (row.site_id is None or None in allowed or row.site_id in allowed):
                assignments.append({"id": row.pk, "name": row.role.name, "site_id": row.site_id,
                                    "permissions": permissions})
    users = get_user_model().objects.filter(is_active=True).exclude(pk=user.pk).order_by("username")
    return {
        "items": [serialize(row, now) for row in rows.order_by("-id")],
        "assignments": assignments, "sites": list(sites.values("id", "name")),
        "all_sites": None in allowed, "users": list(users.values("id", "username")) if assignments else [],
        "username": user.get_username(),
    }


@endpoint("POST", admin=True)
def delegation_decision(request, pk):
    action = body(request).get("action")
    decisions = {"approve": Delegation.Status.APPROVED, "reject": Delegation.Status.REJECTED,
                 "revoke": Delegation.Status.REVOKED}
    if not isinstance(action, str) or action not in decisions:
        raise Problem("invalid_delegation_action")
    with transaction.atomic():
        grant = Delegation.objects.select_for_update().filter(pk=pk).first()
        if grant is None:
            raise Problem("not_found", 404)
        now = timezone.now()
        if action == "approve" and request.user.pk in (grant.requester_id, grant.delegate_id):
            raise Problem("forbidden", 403)
        expected = Delegation.Status.APPROVED if action == "revoke" else Delegation.Status.PENDING
        if grant.status != expected or grant.ends_at <= now:
            raise Problem("invalid_delegation_state", 409)
        if action == "approve" and (
            not grant.requester.is_active or not grant.delegate.is_active
            or not can_request(grant.requester, grant.site_id)
            or set(grant.permissions) != delegated_permissions(grant)
        ):
            raise Problem("invalid_delegation_permissions", 409)
        grant.status = decisions[action]
        grant.save(update_fields=["status"])
        audit(request.user, f"delegation.{action}", grant.pk)
        if action == "revoke":
            parent = grant
            while (child := parent.extensions.select_for_update().filter(
                status__in=[Delegation.Status.PENDING, Delegation.Status.APPROVED]
            ).first()) is not None:
                child.status = Delegation.Status.REVOKED
                child.save(update_fields=["status"])
                audit(request.user, "delegation.revoke", child.pk)
                parent = child
        return serialize(grant, now)
