from backbone.access import has_permission, scopes
from backbone.http import Problem, require
from django.db.models import Q


def allowed_rows(rows, user, permission, field="site_id"):
    allowed = scopes(user, permission)
    return rows if None in allowed else rows.filter(**{field + "__in": allowed})


def own_or_scoped(rows, user, permission, self_permission, own="user_id", field="site_id"):
    allowed = scopes(user, permission)
    if None in allowed:
        return rows
    site = Q(**{field + "__in": allowed})
    own_sites = scopes(user, self_permission)
    own_scope = Q() if None in own_sites else Q(**{field + "__in": own_sites})
    site |= Q(**{own: user.pk}) & own_scope
    return rows.filter(site)


def authorize(user, employee, permission, self_permission):
    if employee.user_id != user.pk or not has_permission(user, self_permission, employee.site_id):
        require(user, permission, employee.site_id)


def one(rows, pk):
    row = rows.filter(pk=pk).first()
    if row is None:
        raise Problem("not_found", 404)
    return row
