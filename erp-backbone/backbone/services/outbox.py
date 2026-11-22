from datetime import timedelta
from uuid import uuid4

import redis
from kombu.exceptions import OperationalError
from django.conf import settings
from django.db import transaction
from django.db.models import Min
from django.utils import timezone

from backbone.models import DeliveryStatus, NotificationDelivery, OutboxEvent

FINAL = ("sent", "skipped", "failed")
LEASE = timedelta(seconds=60)


@transaction.atomic
def enqueue(*, key, module, object_id, event, recipients):
    """Persist intent inside the caller's transaction. No broker or SMTP IO."""
    row, created = OutboxEvent.objects.get_or_create(key=key, defaults={
        "module": module, "object_id": object_id, "event": event,
    })
    if created:
        from backbone.module_events import subscribe
        subscribe(row)
        channels = ("app", "email") if settings.ERP_SMTP_ENABLED else ("app",)
        now = timezone.now()
        NotificationDelivery.objects.bulk_create([
            NotificationDelivery(event=row, recipient_id=user_id, channel=channel, due_at=now)
            for user_id in set(recipients) if user_id for channel in channels
        ])


def claim():
    now = timezone.now()
    with transaction.atomic():
        row = NotificationDelivery.objects.select_for_update(skip_locked=True).filter(
            state__in=("pending","queued","running"), due_at__lte=now).order_by("due_at","pk").first()
        if row is None:
            return None
        row.lease = uuid4()
        row.state, row.due_at = "queued", now+LEASE
        row.save(update_fields=["lease","state","due_at"])
        return row.pk, row.lease


def pump(limit=30, *, on_ready=None):
    from backbone.celeryapp import app

    try:
        broker = redis.Redis.from_url(settings.CELERY_BROKER_URL, socket_connect_timeout=2, socket_timeout=2)
        try:
            broker.ping()
        finally:
            broker.close()
    except (redis.RedisError, OSError):
        DeliveryStatus.objects.update_or_create(pk=1, defaults={"heartbeat":timezone.now(),"broker_ok":False})
        return 0
    DeliveryStatus.objects.update_or_create(pk=1, defaults={"heartbeat":timezone.now(),"broker_ok":True})
    count = 0
    for _ in range(limit):
        item = claim()
        if item is None:
            break
        key, lease = item
        try:
            app.send_task("backbone.tasks.deliver_notification", args=(key,str(lease)), queue="notifications", retry=False)
        except (OSError, redis.RedisError, OperationalError):
            NotificationDelivery.objects.filter(pk=key,lease=lease,state="queued").update(
                state="pending",due_at=timezone.now()+timedelta(seconds=5))
            DeliveryStatus.objects.filter(pk=1).update(broker_ok=False)
            return count
        count += 1
    if on_ready is not None:
        on_ready()
    return count


def workspace_status(user):
    deliveries = NotificationDelivery.objects.all() if user.is_superuser else NotificationDelivery.objects.filter(recipient=user)
    rows = deliveries.exclude(state__in=FINAL)
    pending = rows.count()
    failed = deliveries.filter(state="failed").count()
    oldest = rows.aggregate(first=Min("event__created_at"))["first"] if pending else None
    worker = DeliveryStatus.objects.filter(pk=1).first()
    now = timezone.now()
    healthy = worker is not None and worker.broker_ok and now-worker.heartbeat < timedelta(seconds=25)
    return {"pending":pending, "failed":failed, "delayed":bool(failed or (pending and (
        not healthy or (oldest is not None and now-oldest >= timedelta(seconds=20))))), "broker_ok":healthy}
