from django.conf import settings
from django.db import models

PROTECT = models.PROTECT
USER = settings.AUTH_USER_MODEL


class SiteSettings(models.Model):
    site = models.OneToOneField("backbone.Site", on_delete=PROTECT, related_name="marketing_settings")
    consent_mode = models.CharField(max_length=8, default="single")


class ProviderAccount(models.Model):
    site = models.ForeignKey("backbone.Site", on_delete=PROTECT)
    channel = models.CharField(max_length=10)
    adapter = models.CharField(max_length=12)
    sender = models.CharField(max_length=160)
    config = models.JSONField(default=dict)
    rate = models.PositiveIntegerField(default=1)
    enabled = models.BooleanField(default=True)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["site", "channel"], name="mkt_provider_per_channel")]


class Contact(models.Model):
    site = models.ForeignKey("backbone.Site", on_delete=PROTECT)
    first_name = models.CharField(max_length=100, blank=True)
    lead = models.ForeignKey("crm.Lead", null=True, on_delete=models.SET_NULL)
    customer = models.ForeignKey("sales.Customer", null=True, on_delete=models.SET_NULL, related_name="marketing_contacts")
    forgotten_at = models.DateTimeField(null=True)
    source_key = models.CharField(max_length=64, blank=True)


class ContactEndpoint(models.Model):
    contact = models.ForeignKey(Contact, on_delete=PROTECT, related_name="endpoints")
    channel = models.CharField(max_length=10)
    address = models.CharField(max_length=254, blank=True)
    endpoint_key = models.CharField(max_length=64)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["contact", "channel"], name="mkt_contact_channel")]


class Consent(models.Model):
    endpoint = models.ForeignKey(ContactEndpoint, on_delete=PROTECT)
    purpose = models.CharField(max_length=20)
    status = models.CharField(max_length=12)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["endpoint", "purpose"], name="mkt_consent_purpose")]


class ConsentHistory(models.Model):
    endpoint = models.ForeignKey(ContactEndpoint, null=True, on_delete=PROTECT)
    endpoint_key = models.CharField(max_length=64)
    channel = models.CharField(max_length=10)
    purpose = models.CharField(max_length=20)
    status = models.CharField(max_length=12)
    source = models.CharField(max_length=30)
    created_at = models.DateTimeField(auto_now_add=True)


class Token(models.Model):
    digest = models.CharField(max_length=64, unique=True)
    consent = models.ForeignKey(Consent, on_delete=PROTECT)
    action = models.CharField(max_length=12)
    expires_at = models.DateTimeField(null=True)
    used_at = models.DateTimeField(null=True)


class Suppression(models.Model):
    site = models.ForeignKey("backbone.Site", on_delete=PROTECT)
    channel = models.CharField(max_length=10)
    endpoint_key = models.CharField(max_length=64)
    purpose = models.CharField(max_length=20, default="*")
    reason = models.CharField(max_length=24)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["site", "channel", "endpoint_key", "purpose", "reason"], name="mkt_suppression_unique")]


class ForgetRequest(models.Model):
    contact = models.OneToOneField(Contact, on_delete=PROTECT)
    actor = models.ForeignKey(USER, on_delete=PROTECT)
    reason = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)


class Segment(models.Model):
    site = models.ForeignKey("backbone.Site", on_delete=PROTECT)
    name = models.CharField(max_length=130)
    contacts = models.ManyToManyField(Contact)


class Template(models.Model):
    site = models.ForeignKey("backbone.Site", on_delete=PROTECT)
    name = models.CharField(max_length=130)
    channel = models.CharField(max_length=10)


class TemplateVersion(models.Model):
    template = models.ForeignKey(Template, on_delete=PROTECT)
    number = models.PositiveIntegerField()
    subject = models.CharField(max_length=240, blank=True)
    body = models.TextField(blank=True)
    provider_template = models.CharField(max_length=120, blank=True)
    locked_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["template", "number"], name="mkt_template_version")]


class Campaign(models.Model):
    site = models.ForeignKey("backbone.Site", on_delete=PROTECT)
    title = models.CharField(max_length=160)
    template_version = models.ForeignKey(TemplateVersion, on_delete=PROTECT)
    channel = models.CharField(max_length=10)
    purpose = models.CharField(max_length=20, default="marketing")
    segments = models.ManyToManyField(Segment)
    creator = models.ForeignKey(USER, on_delete=PROTECT, related_name="+")
    approver = models.ForeignKey(USER, null=True, on_delete=PROTECT, related_name="+")
    provider = models.ForeignKey(ProviderAccount, null=True, on_delete=PROTECT)
    status = models.CharField(max_length=12, default="draft")
    revision = models.PositiveIntegerField(default=1)
    schedule_revision = models.PositiveIntegerField(default=0)
    scheduled_at = models.DateTimeField(null=True)
    schedule_timezone = models.CharField(max_length=80, blank=True)


class CampaignRecipient(models.Model):
    campaign = models.ForeignKey(Campaign, on_delete=PROTECT, related_name="recipients")
    contact = models.ForeignKey(Contact, on_delete=PROTECT)
    endpoint = models.ForeignKey(ContactEndpoint, on_delete=PROTECT)
    endpoint_key = models.CharField(max_length=64)

    class Meta:
        db_table = "marketing_campaign_recipients"
        constraints = [
            models.UniqueConstraint(fields=["campaign", "contact"], name="mkt_campaign_contact"),
            models.UniqueConstraint(fields=["campaign", "endpoint_key"], name="mkt_campaign_address"),
        ]


class Delivery(models.Model):
    site = models.ForeignKey("backbone.Site", on_delete=PROTECT)
    provider = models.ForeignKey(ProviderAccount, on_delete=PROTECT)
    campaign = models.ForeignKey(Campaign, null=True, on_delete=PROTECT)
    template_version = models.ForeignKey(TemplateVersion, null=True, on_delete=PROTECT)
    recipient = models.OneToOneField(CampaignRecipient, null=True, on_delete=PROTECT)
    event = models.OneToOneField("backbone.OutboxEvent", null=True, on_delete=PROTECT)
    endpoint = models.ForeignKey(ContactEndpoint, null=True, on_delete=PROTECT)
    kind = models.CharField(max_length=16)
    address = models.CharField(max_length=254, blank=True)
    subject = models.CharField(max_length=240, blank=True)
    body = models.TextField(blank=True)
    provider_template = models.CharField(max_length=120, blank=True)
    variables = models.JSONField(default=dict)
    token = models.ForeignKey(Token, null=True, on_delete=PROTECT)
    state = models.CharField(max_length=24, default="pending")
    due_at = models.DateTimeField()
    lease = models.UUIDField(null=True)
    attempts = models.PositiveIntegerField(default=0)
    schedule_revision = models.PositiveIntegerField(default=0)

    class Meta:
        indexes = [models.Index(fields=["state", "due_at"])]


class DeliveryAttempt(models.Model):
    delivery = models.ForeignKey(Delivery, on_delete=PROTECT)
    provider = models.ForeignKey(ProviderAccount, on_delete=PROTECT)
    number = models.PositiveIntegerField()
    provider_message_id = models.CharField(max_length=200, null=True)
    result = models.CharField(max_length=30, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["delivery", "number"], name="mkt_delivery_attempt"),
            models.UniqueConstraint(fields=["provider", "provider_message_id"], name="mkt_provider_message"),
        ]


class ProviderEvent(models.Model):
    provider = models.ForeignKey(ProviderAccount, on_delete=PROTECT)
    delivery = models.ForeignKey(Delivery, null=True, on_delete=PROTECT)
    event_key = models.CharField(max_length=64)
    message_id = models.CharField(max_length=200)
    category = models.CharField(max_length=26)
    subtype = models.CharField(max_length=60, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["provider", "event_key"], name="mkt_provider_event")]


class DeadLetter(models.Model):
    delivery = models.OneToOneField(Delivery, on_delete=PROTECT)
    reason = models.CharField(max_length=40)
    created_at = models.DateTimeField(auto_now_add=True)


class SurveyVersion(models.Model):
    site = models.ForeignKey("backbone.Site", on_delete=PROTECT)
    title = models.CharField(max_length=140)
    questions = models.JSONField(default=list)


class SurveyInvitation(models.Model):
    survey = models.ForeignKey(SurveyVersion, on_delete=PROTECT)
    endpoint = models.ForeignKey(ContactEndpoint, on_delete=PROTECT)
    digest = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()


class SurveyResponse(models.Model):
    invitation = models.OneToOneField(SurveyInvitation, on_delete=PROTECT)
    answers = models.JSONField()
    submitted_at = models.DateTimeField(auto_now_add=True)


class AutomationRule(models.Model):
    site = models.ForeignKey("backbone.Site", on_delete=PROTECT)
    campaign = models.ForeignKey(Campaign, on_delete=PROTECT)
    survey = models.ForeignKey(SurveyVersion, on_delete=PROTECT)
    delay_minutes = models.PositiveIntegerField()
    enabled = models.BooleanField(default=True)


class AutomationRun(models.Model):
    rule = models.ForeignKey(AutomationRule, on_delete=PROTECT)
    delivery = models.ForeignKey(Delivery, on_delete=PROTECT)
    invitation = models.OneToOneField(SurveyInvitation, on_delete=PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["rule", "delivery"], name="mkt_automation_once")]
