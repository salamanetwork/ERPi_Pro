from django.db.models import Q

from backbone.access import has_permission, scopes, visible
from backbone.models import Assignment

from .models import Ticket


def tickets_for(user):
    rows = Ticket.objects.all()
    allowed = scopes(user,"helpdesk.view")
    agent_sites = scopes(user,"helpdesk.agent") | scopes(user,"helpdesk.dispatch")
    if None not in allowed:
        rows = rows.filter(site_id__in=allowed)
    if None not in agent_sites:
        rows = rows.filter(Q(requester=user) | Q(site_id__in=agent_sites))
    return rows


def is_agent(user, site):
    return has_permission(user,"helpdesk.agent",site) or has_permission(user,"helpdesk.dispatch",site)


def can_use_internal_notes(user, ticket):
    if user.pk == ticket.requester_id:
        return False
    return Assignment.objects.filter(user=user).filter(
        Q(site_id=ticket.site_id) | Q(site_id__isnull=True),
        Q(role__permissions__contains=["helpdesk.agent"])
        | Q(role__permissions__contains=["helpdesk.dispatch"]),
    ).exists()


def site_staff(site, permission):
    users = Assignment.objects.filter(Q(site_id=site) | Q(site__isnull=True),user__is_active=True,
                        role__permissions__contains=[permission]).values_list("user_id",flat=True).distinct()
    return list(users)


def work_visible(user):
    from workorders.models import WorkOrder
    from backbone.services.documents import archive_rows

    return visible(WorkOrder.objects.all(),user,"workorders.view").exclude(pk__in=archive_rows("wo").values("object_id"))


def project_tasks_visible(user):
    from projects.access import accessible
    from projects.models import Task

    return Task.objects.filter(project__in=accessible(user))
