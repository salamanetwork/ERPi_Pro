from django.utils import timezone

from .models import Assignment, AuditEvent, Delegation

DELEGATE_PERMISSION = "platform.delegate"
DELEGABLE = frozenset({
    "assets.view", "assets.create", "workorders.view", "workorders.create",
    "workorders.edit", "workorders.submit", "workorders.approve",
    "purchasing.view", "purchasing.create", "purchasing.edit", "purchasing.submit", "purchasing.approve",
})


def direct_scopes(user, permission):
    if user.is_superuser:
        return {None}
    return {
        row.site_id for row in Assignment.objects.filter(user=user).select_related("role")
        if permission in row.role.permissions
    }


def active_delegations(user):
    now = timezone.now()
    return Delegation.objects.filter(
        delegate=user, requester__is_active=True, status=Delegation.Status.APPROVED,
        starts_at__lte=now, ends_at__gt=now, source_assignment__isnull=False,
    ).select_related("role", "source_assignment")


def delegated_permissions(grant):
    assignment = grant.source_assignment
    if assignment is None or assignment.user_id != grant.requester_id or assignment.role_id != grant.role_id:
        return set()
    if assignment.site_id is not None and assignment.site_id != grant.site_id:
        return set()
    return DELEGABLE.intersection(grant.permissions, grant.role.permissions)


def preload_scopes(user):
    """Reuse authorization reads inside a read-only request."""
    if not user.is_superuser:
        assignments = list(Assignment.objects.filter(user=user).select_related("role"))
        user._erp_assignments = [(assignment.site_id, assignment.role.permissions) for assignment in assignments]
        user._erp_navigation_roles = [assignment.role.hidden_modules for assignment in assignments]
        user._erp_delegations = [(grant.site_id, delegated_permissions(grant)) for grant in active_delegations(user)]


def scopes(user, permission):
    if hasattr(user, "_erp_assignments"):
        return {site for site, permissions in (*user._erp_assignments, *user._erp_delegations)
                if permission in permissions}
    direct = direct_scopes(user, permission)
    if None in direct or permission not in DELEGABLE:
        return direct
    return direct | {grant.site_id for grant in active_delegations(user) if permission in delegated_permissions(grant)}


def has_permission(user, permission, site_id=...):
    allowed = scopes(user, permission)
    return bool(allowed) if site_id is ... else None in allowed or site_id in allowed


def visible(queryset, user, permission):
    allowed = scopes(user, permission)
    return queryset if None in allowed else queryset.filter(site_id__in=allowed)


def audit(user, action, target):
    AuditEvent.objects.create(actor_user_id=user.pk, actor=user.get_username(), action=action, target=str(target))
