from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from backbone.models import ExperienceNotice, NotificationDelivery
from backbone.registry import enabled_modules
from backbone.services.outbox import LEASE


def authorized(delivery):
    event = delivery.event
    if not delivery.recipient.is_active or event.module not in enabled_modules():
        return None
    if event.module == "helpdesk":
        from helpdesk.access import tickets_for

        return tickets_for(delivery.recipient).filter(pk=event.object_id).first()
    return None


@shared_task
def deliver_notification(pk, token):
    with transaction.atomic():
        row = NotificationDelivery.objects.select_related("recipient","event").select_for_update(of=("self",)).filter(pk=pk).first()
        if row is None or str(row.lease) != token or row.state != "queued":
            return
        ticket = authorized(row)
        if ticket is None or (row.channel == "email" and (not settings.ERP_SMTP_ENABLED or not row.recipient.email)):
            row.state = "skipped"
            row.save(update_fields=["state"])
            return
        if row.channel == "app":
            ExperienceNotice.objects.get_or_create(user=row.recipient,key=f"helpdesk:{row.event_id}",defaults={
                "kind":"helpdesk","object_id":ticket.pk,"event":row.event.event,
            })
            row.state = "sent"
            row.save(update_fields=["state"])
            return
        row.attempts += 1
        row.state, row.due_at = "running", timezone.now()+LEASE
        row.save(update_fields=["attempts","state","due_at"])
    try:
        send_mail(f"ERP Helpdesk · {ticket.number}", "An internal ticket was updated. Open Helpdesk to review it.",
                  settings.DEFAULT_FROM_EMAIL, [row.recipient.email])
    except Exception as error:
        NotificationDelivery.objects.filter(pk=pk,lease=row.lease,state="running").update(
            state="failed" if row.attempts >= 6 else "pending", last_error=type(error).__name__[:80],
            due_at=timezone.now()+timedelta(seconds=min(300,2**row.attempts*3)))
    else:
        NotificationDelivery.objects.filter(pk=pk,lease=row.lease,state="running").update(state="sent",last_error="")
