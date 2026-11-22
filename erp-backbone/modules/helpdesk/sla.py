from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from backbone.services.outbox import enqueue

from .access import site_staff
from .models import Ticket, TicketEvent


def schedule_overdue(limit=50):
    now = timezone.now()
    for kind, due, actual in (
        ("first_response_overdue", "first_response_due", "first_response_at"),
        ("resolution_overdue", "resolution_due", "resolved_at"),
    ):
        previous = TicketEvent.objects.filter(ticket_id=OuterRef("pk"), event_type=kind)
        with transaction.atomic():
            tickets = list(Ticket.objects.filter(
                **{due + "__lte": now, actual + "__isnull": True}
            ).exclude(status__in=("resolved", "closed")).filter(
                ~Exists(previous)
            ).order_by(due, "pk").select_for_update(skip_locked=True)[:limit])
            for ticket in tickets:
                if TicketEvent.objects.filter(ticket=ticket, event_type=kind).exists():
                    continue
                item = TicketEvent.objects.create(ticket=ticket, event_type=kind, internal=True)
                recipients = set(site_staff(ticket.site_id, "helpdesk.dispatch"))
                recipients.add(ticket.assignee_id)
                enqueue(key=f"helpdesk:{item.pk}", module="helpdesk", object_id=ticket.pk,
                        event="helpdesk_sla_first_response" if kind == "first_response_overdue" else "helpdesk_sla_resolution", recipients=recipients)
