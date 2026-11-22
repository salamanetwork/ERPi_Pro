from django.conf import settings
from django.db import models


class Site(models.Model):
    description = models.CharField(max_length=500, blank=True, default="", db_default="")
    name = models.CharField(max_length=120, unique=True)
    active = models.BooleanField(default=True)
    revision = models.PositiveIntegerField(default=1)


class Profile(models.Model):
    iam_display_name = models.CharField(max_length=150, blank=True, db_default="")
    iam_revision = models.PositiveIntegerField(default=1, db_default=1)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    language = models.CharField(max_length=2, default="en")
    theme = models.CharField(max_length=8, default="system")
    middle_name = models.CharField(max_length=150, blank=True)
    module_view = models.CharField(max_length=4, default="list")


class ModuleState(models.Model):
    module_id = models.CharField(max_length=80, primary_key=True)
    enabled = models.BooleanField(default=False)


class Role(models.Model):
    revision = models.PositiveIntegerField(default=1, db_default=1)
    name = models.CharField(max_length=120, unique=True)
    permissions = models.JSONField(default=list)
    hidden_modules = models.JSONField(default=list)


class Assignment(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    role = models.ForeignKey(Role, on_delete=models.CASCADE)
    site = models.ForeignKey(Site, on_delete=models.PROTECT, null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "role", "site"], name="assignment_site_unique"),
            models.UniqueConstraint(fields=["user", "role"], condition=models.Q(site__isnull=True),
                                    name="assignment_global_unique"),
        ]


class AuditEvent(models.Model):
    actor = models.CharField(max_length=150)
    actor_user_id = models.PositiveBigIntegerField(null=True, blank=True)
    action = models.CharField(max_length=100)
    target = models.TextField()
    occurred_at = models.DateTimeField(auto_now_add=True)


class CreateReceipt(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    operation = models.CharField(max_length=80)
    key = models.CharField(max_length=120)
    fingerprint = models.CharField(max_length=64)
    response = models.JSONField(default=dict)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "operation", "key"], name="create_receipt_unique"),
        ]


class Authenticator(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    encrypted_key = models.CharField(max_length=255, blank=True)
    last_step = models.BigIntegerField(default=-1)
    failures = models.PositiveSmallIntegerField(default=0)
    blocked_until = models.DateTimeField(null=True)


class Delegation(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        REVOKED = "revoked", "Revoked"

    requester = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    delegate = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    source_assignment = models.ForeignKey(Assignment, on_delete=models.SET_NULL, null=True, related_name="+")
    role = models.ForeignKey(Role, on_delete=models.PROTECT)
    site = models.ForeignKey(Site, on_delete=models.PROTECT, null=True)
    permissions = models.JSONField()
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    reason = models.CharField(max_length=500)
    status = models.CharField(max_length=12, choices=Status, default=Status.PENDING)
    previous = models.ForeignKey("self", on_delete=models.PROTECT, null=True, related_name="extensions")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["delegate", "status", "starts_at", "ends_at"])]
        constraints = [
            models.CheckConstraint(condition=models.Q(ends_at__gt=models.F("starts_at")), name="delegation_valid_window"),
            models.CheckConstraint(condition=~models.Q(requester=models.F("delegate")), name="delegation_distinct_users"),
            models.UniqueConstraint(fields=["previous"], condition=models.Q(status__in=["pending", "approved"]),
                                    name="delegation_one_current_extension"),
        ]


class NumberingRule(models.Model):
    kind = models.SlugField(max_length=30, unique=True)
    prefix = models.CharField(max_length=16, blank=True)
    per_site = models.BooleanField(default=False)
    start = models.PositiveBigIntegerField(default=1)
    revision = models.PositiveIntegerField(default=1)


class NumberingCounter(models.Model):
    rule = models.ForeignKey(NumberingRule, on_delete=models.PROTECT, related_name="counters")
    scope = models.PositiveBigIntegerField(default=0)
    last = models.PositiveBigIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["rule", "scope"], name="numbering_scope_unique")]


class Attachment(models.Model):
    kind = models.SlugField(max_length=30)
    object_id = models.PositiveBigIntegerField()
    name = models.CharField(max_length=160)
    storage_key = models.UUIDField(unique=True, editable=False)
    size = models.PositiveIntegerField()
    created_by = models.CharField(max_length=150)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["kind", "object_id"])]


class ArchivedDocument(models.Model):
    kind = models.SlugField(max_length=30)
    object_id = models.PositiveBigIntegerField()
    archived_by = models.CharField(max_length=150)
    archived_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["kind", "object_id"], name="archive_document_unique")]


class PrintTemplate(models.Model):
    kind = models.SlugField(max_length=30)
    language = models.CharField(max_length=2)
    heading = models.CharField(max_length=120, blank=True)
    company_name = models.CharField(max_length=120, blank=True)
    footer = models.CharField(max_length=240, blank=True)
    logo = models.TextField(blank=True)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["kind", "language"], name="print_template_language_unique")]


class InboxTask(models.Model):
    kind = models.SlugField(max_length=30)
    object_id = models.PositiveBigIntegerField()
    step_key = models.CharField(max_length=100)
    role = models.ForeignKey(Role, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["kind", "object_id", "step_key"], name="inbox_task_step_unique"),
        ]


class ExperienceNotice(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    key = models.CharField(max_length=120)
    kind = models.SlugField(max_length=30)
    object_id = models.PositiveBigIntegerField()
    event = models.CharField(max_length=30)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "key"], name="experience_notice_unique")]


class NoticeRead(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    key = models.CharField(max_length=120)
    read_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "key"], name="experience_read_unique")]


class SavedView(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    screen = models.SlugField(max_length=30)
    name = models.CharField(max_length=80)
    filters = models.JSONField(default=dict)
    is_default = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "screen", "name"], name="saved_view_name_unique"),
            models.UniqueConstraint(fields=["user", "screen"], condition=models.Q(is_default=True),
                                    name="saved_view_default_unique"),
        ]


class NavigationSettings(models.Model):
    hidden_screens = models.JSONField(default=list)


class ProfilePicture(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    image = models.BinaryField()
    revision = models.UUIDField(editable=False)


class CompanyProfile(models.Model):
    name = models.CharField(max_length=120, blank=True)
    logo = models.BinaryField(default=bytes, blank=True)
    logo_type = models.CharField(max_length=24, blank=True)
    revision = models.PositiveIntegerField(default=0)


class OutboxEvent(models.Model):
    key = models.CharField(max_length=120, unique=True)
    module = models.SlugField(max_length=30)
    object_id = models.PositiveBigIntegerField()
    event = models.SlugField(max_length=80)
    created_at = models.DateTimeField(auto_now_add=True)


class NotificationDelivery(models.Model):
    event = models.ForeignKey(OutboxEvent, on_delete=models.CASCADE)
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    channel = models.CharField(max_length=10)
    state = models.CharField(max_length=12, default="pending")
    due_at = models.DateTimeField()
    lease = models.UUIDField(null=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.CharField(max_length=80, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["event", "recipient", "channel"], name="outbox_unique_channel")]
        indexes = [models.Index(fields=["state", "due_at"], name="outbox_due_idx")]


class DeliveryStatus(models.Model):
    heartbeat = models.DateTimeField()
    broker_ok = models.BooleanField(default=False)


class ModuleEventDelivery(models.Model):
    event_row = models.ForeignKey(OutboxEvent, on_delete=models.PROTECT)
    subscriber = models.SlugField(max_length=80)
    due_at = models.DateTimeField(db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    completed_at = models.DateTimeField(null=True)
    last_error = models.CharField(max_length=120, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["event_row","subscriber"],name="erpi_event_subscriber")]

class ApiConnector(models.Model):
    slug=models.SlugField(unique=True)
    name=models.CharField(max_length=120)
    site=models.ForeignKey(Site,on_delete=models.PROTECT,null=True,blank=True)
    base_url=models.URLField()
    health_path=models.CharField(max_length=200,default='/')
    auth_type=models.CharField(max_length=20,choices=[(x,x) for x in ('none','bearer','basic','api_key_header')],default='none')
    auth_header_name=models.CharField(max_length=80,default='Authorization')
    encrypted_credentials=models.TextField(blank=True)
    timeout_seconds=models.PositiveSmallIntegerField(default=10)
    max_retries=models.PositiveSmallIntegerField(default=2)
    active=models.BooleanField(default=True)
    revision=models.PositiveIntegerField(default=1)

class OutboundApiLog(models.Model):
    connector=models.ForeignKey(ApiConnector,on_delete=models.PROTECT)
    method=models.CharField(max_length=8)
    path=models.CharField(max_length=200)
    status_code=models.PositiveSmallIntegerField(null=True)
    duration_ms=models.PositiveIntegerField()
    success=models.BooleanField()
    error_code=models.CharField(max_length=40,blank=True)
    created_at=models.DateTimeField(auto_now_add=True)

class WebhookEndpoint(models.Model):
    slug=models.SlugField(unique=True)
    name=models.CharField(max_length=120)
    connector=models.ForeignKey(ApiConnector,on_delete=models.PROTECT,null=True,blank=True)
    site=models.ForeignKey(Site,on_delete=models.PROTECT,null=True,blank=True)
    encrypted_secret=models.TextField()
    signature_header=models.CharField(max_length=80,default='X-Webhook-Signature')
    timestamp_header=models.CharField(max_length=80,default='X-Webhook-Timestamp',blank=True)
    tolerance_seconds=models.PositiveIntegerField(default=300)
    active=models.BooleanField(default=True)
    revision=models.PositiveIntegerField(default=1)

class WebhookDelivery(models.Model):
    endpoint=models.ForeignKey(WebhookEndpoint,on_delete=models.PROTECT)
    delivery_id=models.CharField(max_length=128)
    status=models.CharField(max_length=12,default='accepted')
    payload_sha256=models.CharField(max_length=64)
    payload=models.BinaryField()
    received_at=models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['endpoint','delivery_id'],name='webhook_endpoint_delivery_unique')]

class PersonalMenu(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    layout = models.JSONField(default=dict)
    revision = models.PositiveIntegerField(default=0)

class DashboardPreference(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    layout = models.JSONField(default=dict)
    revision = models.PositiveIntegerField(default=0)

# Revocation follows the direct assignment even for bulk removals and extensions.
from django.db.models.signals import pre_delete
from django.dispatch import receiver

@receiver(pre_delete, sender=Assignment, dispatch_uid='backbone.revoke_delegations_for_assignment')
def revoke_assignment_delegations(sender, instance, using, **kwargs):
    for grant in Delegation.objects.using(using).select_for_update().filter(
        source_assignment=instance, status__in=(Delegation.Status.PENDING, Delegation.Status.APPROVED)
    ):
        grant.status = Delegation.Status.REVOKED
        grant.save(using=using, update_fields=['status'])
        AuditEvent.objects.using(using).create(actor='system', action='delegation.source_revoked', target=str(grant.pk))
