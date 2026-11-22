from .access import active_delegations, delegated_permissions
from .models import Assignment, Role


def reviewer_scopes(user, module):
    approval = f"{module}.approve"
    permissions_needed = {f"{module}.view", approval}
    direct = {
        (role_id, site_id)
        for role_id, site_id, permissions in Assignment.objects.filter(user=user)
        .values_list("role_id", "site_id", "role__permissions")
        if approval in permissions
    }
    grants = [grant for grant in active_delegations(user).order_by("id")
              if permissions_needed.issubset(delegated_permissions(grant))]
    return direct, grants


def review_authority(user, submitter_id, role_id, site_id, scopes):
    if submitter_id == user.pk:
        return False, None
    direct, grants = scopes
    if (role_id, None) in direct or (role_id, site_id) in direct:
        return True, None
    grant = next((item for item in grants
                  if item.role_id == role_id and item.site_id in (None, site_id)
                  and item.requester_id != submitter_id), None)
    return grant is not None, grant


def reviewer_roles(module):
    needed = {f"{module}.view", f"{module}.approve"}
    return [{"id": role.pk, "name": role.name} for role in Role.objects.order_by("name")
            if needed.issubset(role.permissions)]
