"""Transactional at-least-once deliveries for installed, trusted module hooks."""
from datetime import timedelta
from importlib import import_module
from django.db import transaction
from django.utils import timezone
from .models import ModuleEventDelivery
from .registry import MANIFESTS,enabled_modules


def subscribe(row):
    topic=f"{row.module}.{row.event}"
    if row.module != "webhook" and row.event not in MANIFESTS.get(row.module,{}).get("events_published",[]):return
    listeners=[key for key,m in MANIFESTS.items() if topic in m.get("events_subscribed",[])]
    if listeners:
        active=enabled_modules()
        ModuleEventDelivery.objects.bulk_create([
            ModuleEventDelivery(event_row=row,subscriber=k,due_at=timezone.now())
            for k in listeners if k in active],ignore_conflicts=True)


def publish(module, event, object_id, key):
    from .services.outbox import enqueue
    if event not in MANIFESTS[module]["events_published"]:
        raise ValueError("Undeclared publication: "+event)
    enqueue(module=module,event=event,object_id=object_id,key=key,recipients=[])


def dispatch(limit=30):
    ready=enabled_modules()
    for _ in range(limit):
        with transaction.atomic():
            job=ModuleEventDelivery.objects.select_for_update(skip_locked=True,of=("self",)).select_related("event_row").filter(
                completed_at=None,subscriber__in=ready,due_at__lte=timezone.now()).order_by("due_at","pk").first()
            if not job:break
            try:
                with transaction.atomic():
                    import_module(job.subscriber+".events").handle_event(job.event_row)
            except Exception as exc:
                job.attempts+=1
                job.due_at=timezone.now()+timedelta(seconds=min(3600,2**min(12,job.attempts)))
                job.last_error=type(exc).__name__[:120]
            else:
                job.completed_at=timezone.now()
                job.last_error=""
            job.save()
