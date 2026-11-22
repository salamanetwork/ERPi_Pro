from .models import Ticket


def open_tickets(site_id=None):
    rows = Ticket.objects.exclude(status="closed")
    return rows.filter(site_id=site_id) if site_id is not None else rows
