import hashlib

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from backbone.access import visible
from backbone.models import Assignment, ExperienceNotice, NoticeRead
from inventory.models import SiteSettings

from . import models as m
from .providers import ProviderFailure
from .services import automate, failure, finish


def notify_admins(site_id, key, kind, object_id):
    ids = Assignment.objects.filter(
        Q(site_id=site_id) | Q(site_id__isnull=True), role__permissions__contains=["marketing.admin"],
        user__is_active=True,
    ).values_list("user_id", flat=True).distinct()
    ExperienceNotice.objects.bulk_create([
        ExperienceNotice(user_id=user, key=key, kind=kind, event=kind, object_id=object_id) for user in ids
    ], ignore_conflicts=True)


def accessible_notices(user):
    campaigns = visible(m.Campaign.objects.all(), user, "marketing.admin")
    events = visible(m.Contact.objects.all(), user, "marketing.admin")
    complaints = m.ProviderEvent.objects.filter(delivery__endpoint__contact_id__in=events.values("pk"))
    return ExperienceNotice.objects.filter(user=user).filter(
        Q(kind="marketing_timezone", object_id__in=campaigns.values("pk"))
        | Q(kind="marketing_complaint", object_id__in=complaints.values("pk"))
    )


def notice_rows(user, site=None, limit=100):
    rows = accessible_notices(user)
    if site is not None:
        rows = rows.filter(Q(kind="marketing_timezone",object_id__in=m.Campaign.objects.filter(site_id=site).values("pk"))
            | Q(kind="marketing_complaint",object_id__in=m.ProviderEvent.objects.filter(provider__site_id=site).values("pk")))
    for row in rows.order_by("-id")[:limit]:
        yield {"key":row.key,"number":f"MKT-{row.object_id}","route":"/marketing",
            "event":row.event,"created_at":row.created_at.isoformat()}


def timezone_alerts():
    zones = dict(SiteSettings.objects.values_list("site_id", "timezone"))
    for campaign in m.Campaign.objects.filter(status__in=["scheduled","paused"],scheduled_at__gt=timezone.now()).iterator():
        zone = zones.get(campaign.site_id,"UTC")
        if campaign.schedule_timezone!=zone:
            version = hashlib.sha256(f"{campaign.schedule_revision}:{zone}".encode()).hexdigest()[:16]
            notify_admins(campaign.site_id, f"marketing:timezone:{campaign.pk}:{version}", "marketing_timezone", campaign.pk)


@transaction.atomic
def record(account,key,message_id,category,subtype):
    attempt=m.DeliveryAttempt.objects.filter(provider=account,provider_message_id=message_id).first()
    if attempt is None:
        m.ProviderEvent.objects.get_or_create(provider=account,event_key=key,defaults={
            "message_id":message_id,"category":category,"subtype":subtype,
        })
        return
    row=m.Delivery.objects.select_for_update(of=("self",)).select_related("endpoint__contact").get(pk=attempt.delivery_id)
    event,fresh=m.ProviderEvent.objects.get_or_create(provider=account,event_key=key,defaults={
        "delivery":row,"message_id":message_id,"category":category,"subtype":subtype,
    })
    if not fresh and event.delivery_id is not None:
        return
    if not fresh:
        event.delivery=row
        event.save(update_fields=["delivery"])
    if category in {"hard_bounce","complaint"} and row.endpoint_id and row.kind!="test":
        m.Suppression.objects.get_or_create(site=row.site,channel=row.provider.channel,
            endpoint_key=row.endpoint.endpoint_key,reason=category)
        if category=="complaint":
            notify_admins(row.site_id,f"marketing:complaint:{event.pk}","marketing_complaint",event.pk)
        if row.state!="skipped_forgotten":
            finish(row,"bounced" if category=="hard_bounce" else "complained")
        return
    if row.state!="accepted" or attempt.number!=row.attempts:
        return
    if category=="delivered":
        finish(row,"delivered")
        automate(row)
    elif category=="soft_bounce":
        failure(row,ProviderFailure("soft_bounce",retry=row.kind!="test" and subtype in {"General","MailboxFull"}))
    elif category=="bounce_unknown":
        finish(row,"unknown")
    elif category in {"hard_bounce","complaint","failed"}:
        failure(row,ProviderFailure(category))


def reconcile_events(provider_id, message_id):
    account = m.ProviderAccount.objects.get(pk=provider_id)
    for event in m.ProviderEvent.objects.filter(provider=account,message_id=message_id,delivery=None).order_by("pk"):
        record(account,event.event_key,message_id,event.category,event.subtype)
