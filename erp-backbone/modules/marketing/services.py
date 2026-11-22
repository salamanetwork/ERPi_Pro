import hashlib
import hmac
import random
import re
from datetime import timedelta
from uuid import uuid4

from celery import shared_task
from django.conf import settings
from django.core.signing import Signer
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from kombu.exceptions import OperationalError
from redis import Redis, RedisError

from backbone.access import audit
from backbone.celeryapp import app
from backbone.http import Problem
from backbone.models import OutboxEvent
from backbone.registry import enabled_modules

from . import models as m
from .providers import PROVIDERS, ProviderFailure, enabled, mail

CHANNELS = ("email", "sms", "whatsapp")
PURPOSES = ("marketing", "utility")
VARIABLE = re.compile(r"{{\s*([a-z][a-z0-9_]*)\s*}}")
LEASE = timedelta(seconds=90)


def fingerprint(value):
    return hmac.new(settings.ERP_MARKETING_HMAC_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()


def token_value(token):
    return Signer(salt="marketing.capability").sign(str(token.pk))


def new_token(consent, action, expires_at=None):
    token = m.Token.objects.create(digest=uuid4().hex, consent=consent, action=action, expires_at=expires_at)
    token.digest = fingerprint(token_value(token))
    token.save(update_fields=["digest"])
    return token


def consent_state(consent, state, source):
    if consent.status != state:
        consent.status = state
        consent.save(update_fields=["status"])
        m.ConsentHistory.objects.create(endpoint=consent.endpoint, endpoint_key=consent.endpoint.endpoint_key,
            channel=consent.endpoint.channel, purpose=consent.purpose, status=state, source=source)


def provider_for(site, channel):
    account = m.ProviderAccount.objects.filter(site=site, channel=channel, enabled=True).first()
    if account is None and settings.ERP_MARKETING_LOCAL:
        from backbone.models import Site

        with transaction.atomic():
            location = Site.objects.select_for_update().get(pk=site)
            account, _ = m.ProviderAccount.objects.get_or_create(site=location, channel=channel, defaults={
                "adapter": "mailpit" if channel == "email" else "mock",
                "sender": "marketing@erp.test" if channel == "email" else "+15005550006",
            })
    if account is None or not enabled(account):
        raise Problem("marketing_provider_unavailable", 409)
    return account


def suppression_exists(endpoint, purpose):
    return m.Suppression.objects.filter(site=endpoint.contact.site, channel=endpoint.channel,
        endpoint_key=endpoint.endpoint_key, purpose__in=["*", purpose]).exists()


def eligible(endpoint, purpose):
    if endpoint.contact.forgotten_at or not endpoint.address:
        return "skipped_forgotten"
    if suppression_exists(endpoint, purpose):
        return "skipped_suppressed"
    if not m.Consent.objects.filter(endpoint=endpoint, purpose=purpose, status="confirmed").exists():
        return "skipped_consent"
    return ""


def apply_consent(endpoint, purpose, change, source):
    row, _ = m.Consent.objects.get_or_create(endpoint=endpoint, purpose=purpose, defaults={"status": ""})
    if change == "out":
        consent_state(row, "opt_out", source)
        m.Suppression.objects.get_or_create(site=endpoint.contact.site, channel=endpoint.channel,
            endpoint_key=endpoint.endpoint_key, purpose=purpose, reason="opt_out")
    else:
        if m.Suppression.objects.filter(site=endpoint.contact.site, channel=endpoint.channel,
                endpoint_key=endpoint.endpoint_key, reason__in=["hard_bounce", "complaint", "forgotten"]).exists():
            raise Problem("marketing_suppressed", 409)
        if row.status == "confirmed":
            return row
        mode = m.SiteSettings.objects.filter(site=endpoint.contact.site).values_list("consent_mode", flat=True).first()
        if mode == "double":
            mail_endpoint=endpoint if endpoint.channel=="email" else endpoint.contact.endpoints.filter(channel="email").first()
            if not mail_endpoint or not mail_endpoint.address:
                raise Problem("marketing_confirmation_email_required", 409)
            m.Token.objects.filter(consent=row, action="confirm", used_at=None).update(used_at=timezone.now())
            consent_state(row, "pending", source)
            token = new_token(row, "confirm", timezone.now() + timedelta(hours=48))
            enqueue(site=endpoint.contact.site, provider=provider_for(endpoint.contact.site_id, "email"),
                endpoint=mail_endpoint, kind="confirm", address=mail_endpoint.address, token=token,
                subject="Confirm your subscription / تأكيد الاشتراك", body="Confirm your subscription using this link / أكد اشتراكك من الرابط.")
        else:
            confirm(row, source)
    return row


def confirm(consent, source):
    endpoint = consent.endpoint
    m.Suppression.objects.filter(site=endpoint.contact.site, channel=endpoint.channel,
        endpoint_key=endpoint.endpoint_key, purpose=consent.purpose, reason="opt_out").delete()
    consent_state(consent, "confirmed", source)


def enqueue(**values):
    values.setdefault("due_at", timezone.now())
    delivery = m.Delivery.objects.create(**values)
    delivery.event = OutboxEvent.objects.create(key=f"marketing:{delivery.pk}", module="marketing",
        object_id=delivery.pk, event="send")
    delivery.save(update_fields=["event"])
    return delivery


def render(version, contact):
    data = {"first_name": contact.first_name if contact else ""}
    missing = set()

    def substitute(match):
        key = match[1]
        if not data.get(key):
            missing.add(key)
        return data.get(key, "")

    subject = VARIABLE.sub(substitute, version.subject)
    text = VARIABLE.sub(substitute, version.body)
    return subject, text, sorted(missing)


def audience(campaign):
    return m.ContactEndpoint.objects.filter(
        contact__site_id=campaign.site_id, contact__segment__in=campaign.segments.all(), channel=campaign.channel,
    ).select_related("contact").distinct().order_by("contact_id")


def scheduled_deliveries(campaign):
    selected = {}
    for endpoint in audience(campaign):
        previous = selected.get(endpoint.endpoint_key)
        if previous is None or (eligible(previous, campaign.purpose) and not eligible(endpoint, campaign.purpose)):
            selected[endpoint.endpoint_key] = endpoint
    for endpoint in selected.values():
        recipient, fresh = m.CampaignRecipient.objects.get_or_create(campaign=campaign, contact=endpoint.contact,
            defaults={"endpoint":endpoint, "endpoint_key":endpoint.endpoint_key}) if not campaign.recipients.filter(
                endpoint_key=endpoint.endpoint_key).exists() else (None, False)
        if not fresh:
            continue
        enqueue(site=campaign.site, provider=campaign.provider, campaign=campaign, recipient=recipient,
            endpoint=endpoint, kind="campaign", address=endpoint.address, due_at=campaign.scheduled_at,
            schedule_revision=campaign.schedule_revision)


RATE_SCRIPT = """
if (tonumber(redis.call('GET', KEYS[1]) or '0') >= tonumber(ARGV[1])) or
   (tonumber(redis.call('GET', KEYS[2]) or '0') >= tonumber(ARGV[2])) then return 0 end
for i=1,2 do
  if redis.call('INCR',KEYS[i]) == 1 then redis.call('EXPIRE',KEYS[i],2) end
end
return 1
"""


def rate_allowed(row):
    epoch = int(timezone.now().timestamp())
    channel = row.provider.channel
    limit = settings.ERP_MARKETING_CHANNEL_RATE.get(channel, 1)
    with Redis.from_url(settings.CELERY_BROKER_URL, socket_connect_timeout=2, socket_timeout=2) as broker:
        return bool(broker.eval(RATE_SCRIPT, 2, f"erp:mkt:channel:{channel}:{epoch}",
            f"erp:mkt:provider:{row.provider_id}:{epoch}", limit, row.provider.rate))


def dispatch(limit=20):
    try:
        with Redis.from_url(settings.CELERY_BROKER_URL,socket_connect_timeout=2,socket_timeout=2) as broker:
            broker.ping()
    except (RedisError,OSError):
        return
    now = timezone.now()
    with transaction.atomic():
        stale = list(m.Delivery.objects.select_for_update(skip_locked=True)
            .filter(state="running", due_at__lte=now).order_by("due_at").values_list("pk",flat=True)[:limit])
        m.Delivery.objects.filter(pk__in=stale).update(state="unknown", body="", variables={})
        rows = list(m.Delivery.objects.select_for_update(of=("self",), skip_locked=True)
            .filter(state__in=["pending", "queued"], due_at__lte=now)
            .exclude(Q(kind="campaign",campaign__status__in=["draft","paused","approved"]))
            .order_by("due_at","pk")[:limit])
        for row in rows:
            row.lease = uuid4()
            row.state = "queued"
            row.due_at = now + LEASE
        m.Delivery.objects.bulk_update(rows, ["lease","state","due_at"])
    for index, row in enumerate(rows):
        try:
            app.send_task("marketing.services.send_delivery", args=(row.pk, str(row.lease)), queue="marketing", retry=False)
        except (OperationalError, OSError, RedisError):
            leases=Q(pk__in=[])
            for unsubmitted in rows[index:]:
                leases |= Q(pk=unsubmitted.pk,lease=unsubmitted.lease)
            m.Delivery.objects.filter(leases,state="queued").update(state="pending",due_at=now+timedelta(seconds=5))
            break


def finish(row, state):
    row.state = state
    row.save(update_fields=["state"])


def failure(row, problem):
    if problem.unknown:
        finish(row, "unknown")
    elif problem.retry and row.attempts < 5:
        delay = max(problem.delay, min(3600, 30 * 2 ** (row.attempts - 1)) + random.randint(0, 15))
        row.state, row.due_at = "pending", timezone.now() + timedelta(seconds=delay)
        row.save(update_fields=["state", "due_at"])
    else:
        finish(row, "dead")
        m.DeadLetter.objects.update_or_create(delivery=row, defaults={"reason": problem.code})


def due_later(row):
    row.state, row.due_at = "pending", timezone.now() + timedelta(seconds=2)
    row.save(update_fields=["state", "due_at"])


@shared_task(ignore_result=True, soft_time_limit=45, time_limit=55)
def send_delivery(pk, lease):
    with transaction.atomic():
        seed = m.Delivery.objects.filter(pk=pk).values("endpoint_id", "campaign_id", "kind").first()
        if not seed:
            return
        # Lock contact before delivery so forgetting can redact queued messages safely.
        if seed["endpoint_id"]:
            contact_id = m.ContactEndpoint.objects.values_list("contact_id", flat=True).get(pk=seed["endpoint_id"])
            m.Contact.objects.select_for_update().get(pk=contact_id)
        if seed["campaign_id"] and seed["kind"] == "campaign":
            m.Campaign.objects.select_for_update().get(pk=seed["campaign_id"])
        row = m.Delivery.objects.select_related("provider", "endpoint__contact", "campaign__template_version", "token","template_version","site").select_for_update(of=("self",)).get(pk=pk)
        if row.state != "queued" or str(row.lease) != lease:
            return
        if "marketing" not in enabled_modules() or not enabled(row.provider):
            due_later(row)
            return
        if not row.site.active:
            finish(row,"skipped_site")
            return
        if row.endpoint and row.endpoint.contact.forgotten_at:
            finish(row, "skipped_forgotten")
            return
        if row.kind == "campaign":
            if row.campaign.status == "cancelled":
                finish(row, "cancelled")
                return
            if row.campaign.status != "scheduled" or row.campaign.schedule_revision != row.schedule_revision:
                due_later(row)
                return
            row.subject, row.body, missing = render(row.campaign.template_version, row.endpoint.contact)
            if missing:
                finish(row, "skipped_variables")
                return
            row.provider_template = row.campaign.template_version.provider_template
            if row.provider.channel == "whatsapp" and row.provider_template:
                row.variables = {"1": row.endpoint.contact.first_name}
        if row.kind == "test":
            row.subject,row.body,missing=render(row.template_version,row.endpoint.contact if row.endpoint_id else None)
            if missing:
                finish(row,"skipped_variables")
                return
            if row.provider.channel=="whatsapp" and row.endpoint_id:
                row.variables={"1":row.endpoint.contact.first_name}
        if row.endpoint and row.kind in {"campaign", "survey"}:
            purpose = row.campaign.purpose if row.campaign_id else "marketing"
            if result := eligible(row.endpoint, purpose):
                finish(row, result)
                return
            if row.address != row.endpoint.address:
                finish(row, "skipped_address")
                return
            if row.provider.channel == "email" and purpose == "marketing" and not row.token_id:
                consent = m.Consent.objects.get(endpoint=row.endpoint, purpose=purpose)
                row.token = new_token(consent, "unsubscribe")
                row.save(update_fields=["token"])
        if row.kind == "confirm" and (row.token.used_at or row.token.expires_at <= timezone.now()
                or row.token.consent.status != "pending" or m.Suppression.objects.filter(site=row.site,
                    channel=row.endpoint.channel, endpoint_key=row.endpoint.endpoint_key,
                    reason__in=["hard_bounce","complaint","forgotten"]).exists()):
            finish(row, "skipped_consent")
            return
        try:
            allowed = rate_allowed(row)
        except RedisError:
            allowed = False
        if not allowed:
            due_later(row)
            return
        row.attempts += 1
        row.state, row.due_at = "running", timezone.now() + LEASE
        row.save(update_fields=["state", "attempts", "due_at"])
        attempt = m.DeliveryAttempt.objects.create(delivery=row, provider=row.provider, number=row.attempts)
        payload_token = token_value(row.token) if row.token_id else None
    try:
        if row.provider.adapter == "ses" and row.kind == "campaign":
            from .dns import preflight
            if not preflight(row.provider)["ok"]:
                raise ProviderFailure("ses_preflight_failed", retry=True, delay=300)
        if row.kind == "test" and row.provider.channel == "email":
            payload_token=Signer(salt="marketing.test-unsubscribe").sign(str(row.pk))
        if row.endpoint and row.kind in {"campaign","survey"}:
            fresh_endpoint=m.ContactEndpoint.objects.select_related("contact").get(pk=row.endpoint_id)
            purpose=row.campaign.purpose if row.campaign_id else "marketing"
            state=eligible(fresh_endpoint,purpose)
            if state:
                m.Delivery.objects.filter(pk=pk,state="running").update(state=state)
                return
        if not m.Delivery.objects.filter(pk=pk,state="running").exists():
            return
        message = mail(row, attempt.number, payload_token)
        message_id, state = PROVIDERS[row.provider.adapter].send(row.provider, message)
    except ProviderFailure as exc:
        with transaction.atomic():
            row = m.Delivery.objects.select_for_update().get(pk=pk)
            if row.state == "running":
                failure(row, exc)
            attempt.result = exc.code
            attempt.save(update_fields=["result"])
    else:
        with transaction.atomic():
            row = m.Delivery.objects.select_for_update().get(pk=pk)
            attempt.provider_message_id, attempt.result = message_id, state
            attempt.save(update_fields=["provider_message_id", "result"])
            if row.state == "running":
                finish(row, state)
            if state == "delivered":
                automate(row)
            elif state == "accepted":
                from .feedback import reconcile_events

                transaction.on_commit(lambda: reconcile_events(attempt.provider_id, message_id))


def automate(delivery):
    if delivery.kind != "campaign" or not delivery.endpoint_id:
        return
    for rule in m.AutomationRule.objects.filter(campaign=delivery.campaign, enabled=True):
        if m.AutomationRun.objects.filter(rule=rule, delivery=delivery).exists():
            continue
        now = timezone.now()
        due = now + timedelta(minutes=rule.delay_minutes)
        invitation = m.SurveyInvitation.objects.create(survey=rule.survey, endpoint=delivery.endpoint,
            digest=uuid4().hex, expires_at=due + timedelta(days=14))
        token = Signer(salt="marketing.survey").sign(str(invitation.pk))
        invitation.digest = fingerprint(token)
        invitation.save(update_fields=["digest"])
        m.AutomationRun.objects.create(rule=rule, delivery=delivery, invitation=invitation)
        if delivery.provider.channel == "email":
            enqueue(site=delivery.site, provider=delivery.provider, endpoint=delivery.endpoint, kind="survey",
                address=delivery.address, subject=rule.survey.title, due_at=due,
                body=f"Survey / استبيان\n{settings.ERP_MARKETING_PUBLIC_URL}/api/marketing/survey-invitations/{token}")


def expire_consents():
    ids = m.Token.objects.filter(action="confirm", used_at=None, expires_at__lte=timezone.now(),
        consent__status="pending").values_list("pk", flat=True)[:200]
    for pk in list(ids):
        with transaction.atomic():
            token = m.Token.objects.select_for_update().select_related("consent__endpoint").get(pk=pk)
            if not token.used_at:
                consent_state(token.consent, "expired", "expiry")
                token.used_at = timezone.now()
                token.save(update_fields=["used_at"])


def forget(contact, actor, reason):
    request, fresh = m.ForgetRequest.objects.get_or_create(contact=contact, defaults={"actor":actor, "reason":reason})
    if not fresh:
        return request
    now = timezone.now()
    endpoints = contact.endpoints.all()
    for endpoint in endpoints:
        m.Suppression.objects.get_or_create(site=contact.site, channel=endpoint.channel,
            endpoint_key=endpoint.endpoint_key, reason="forgotten")
    m.ConsentHistory.objects.filter(endpoint__in=endpoints).update(endpoint=None)
    m.Token.objects.filter(consent__endpoint__in=endpoints, used_at=None).update(used_at=now)
    m.SurveyInvitation.objects.filter(endpoint__in=endpoints, expires_at__gt=now).update(expires_at=now)
    m.SurveyResponse.objects.filter(invitation__endpoint__in=endpoints).update(answers=[])
    m.Delivery.objects.filter(endpoint__in=endpoints, state__in=["pending","queued","running"]).update(state="skipped_forgotten")
    m.Delivery.objects.filter(endpoint__in=endpoints).update(address="", subject="", body="", variables={})
    endpoints.update(address="")
    contact.first_name, contact.forgotten_at, contact.lead_id, contact.customer_id = "", now, None, None
    contact.save(update_fields=["first_name","forgotten_at","lead","customer"])
    audit(actor, "marketing.forget", f"contact:{contact.pk}")
    return request
