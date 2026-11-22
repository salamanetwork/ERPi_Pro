from django.conf import settings
from django.db import models

USER = settings.AUTH_USER_MODEL
PROTECT = models.PROTECT
Q = models.Q


class Lead(models.Model):
    party = models.ForeignKey("parties.Party", on_delete=models.PROTECT, null=True, blank=True)

    def save(self,*args,**kwargs):
        if self._state.adding and self.party_id is None:
            from parties.models import Party
            from parties.matching import match
            self.party_id = match(self,Party.objects)
        return super().save(*args,**kwargs)

    site = models.ForeignKey("backbone.Site", on_delete=PROTECT)
    owner = models.ForeignKey(USER, on_delete=PROTECT, related_name="+")
    name = models.CharField(max_length=120)
    company = models.CharField(max_length=120, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=48, blank=True)
    source = models.CharField(max_length=80, blank=True)
    state = models.CharField(max_length=16, default="new")
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["site", "state", "owner"])]
        constraints = [models.CheckConstraint(condition=Q(state__in=["new", "qualified", "converted", "disqualified"]),
                                              name="crm_valid_lead_state")]

    @property
    def number(self):
        return f"LEAD-{self.pk:06d}"


class Stage(models.Model):
    code = models.SlugField(max_length=32, unique=True)
    name_en = models.CharField(max_length=80)
    name_ar = models.CharField(max_length=80)
    order = models.PositiveIntegerField()
    active = models.BooleanField(default=True)
    kind = models.CharField(max_length=8, default="open")
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["order", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(kind__in=["open", "won", "lost"]), name="crm_valid_stage_kind"),
            models.UniqueConstraint(fields=["kind"], condition=Q(kind__in=["won", "lost"]),
                                    name="crm_one_terminal_stage_per_kind"),
        ]


class Opportunity(models.Model):
    site = models.ForeignKey("backbone.Site", on_delete=PROTECT)
    owner = models.ForeignKey(USER, on_delete=PROTECT, related_name="+")
    lead = models.OneToOneField(Lead, on_delete=PROTECT, null=True, related_name="opportunity")
    customer = models.ForeignKey("sales.Customer", on_delete=PROTECT, null=True)
    stage = models.ForeignKey(Stage, on_delete=PROTECT)
    title = models.CharField(max_length=160)
    estimated_amount = models.DecimalField(max_digits=22, decimal_places=2)
    currency = models.CharField(max_length=3)
    expected_close = models.DateField(null=True)
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["site", "stage", "owner"]), models.Index(fields=["owner", "stage"])]
        constraints = [models.CheckConstraint(condition=Q(estimated_amount__gte=0), name="crm_nonnegative_estimate")]

    @property
    def number(self):
        return f"OPP-{self.pk:06d}"


class StageHistory(models.Model):
    opportunity = models.ForeignKey(Opportunity, on_delete=PROTECT, related_name="history")
    previous_stage = models.ForeignKey(Stage, on_delete=PROTECT, null=True, related_name="+")
    stage = models.ForeignKey(Stage, on_delete=PROTECT, related_name="+")
    previous_names = models.JSONField(default=dict)
    stage_names = models.JSONField(default=dict)
    actor = models.ForeignKey(USER, on_delete=PROTECT, related_name="+")
    reason = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class Activity(models.Model):
    site = models.ForeignKey("backbone.Site", on_delete=PROTECT)
    owner = models.ForeignKey(USER, on_delete=PROTECT, related_name="+")
    lead = models.ForeignKey(Lead, on_delete=PROTECT, null=True, related_name="activities")
    opportunity = models.ForeignKey(Opportunity, on_delete=PROTECT, null=True, related_name="activities")
    kind = models.CharField(max_length=12)
    title = models.CharField(max_length=160)
    notes = models.CharField(max_length=1000, blank=True)
    due_at = models.DateTimeField(null=True)
    schedule_version = models.PositiveIntegerField(default=1)
    state = models.CharField(max_length=10, default="open")
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["owner", "due_at"], condition=Q(state="open"), name="crm_open_activity_due")]
        constraints = [
            models.CheckConstraint(condition=Q(lead__isnull=False, opportunity__isnull=True) |
                                              Q(lead__isnull=True, opportunity__isnull=False), name="crm_activity_single_parent"),
            models.CheckConstraint(condition=Q(kind="note") | Q(due_at__isnull=False), name="crm_scheduled_activity_due"),
        ]

    @property
    def number(self):
        return f"ACT-{self.pk:06d}"


class CustomerRequest(models.Model):
    site = models.ForeignKey("backbone.Site", on_delete=PROTECT)
    opportunity = models.ForeignKey(Opportunity, on_delete=PROTECT, related_name="customer_requests")
    fields = models.JSONField()
    state = models.CharField(max_length=10, default="pending")
    requested_by = models.ForeignKey(USER, on_delete=PROTECT, related_name="+")
    decided_by = models.ForeignKey(USER, on_delete=PROTECT, null=True, related_name="+")
    customer = models.ForeignKey("sales.Customer", on_delete=PROTECT, null=True)
    reason = models.CharField(max_length=500, blank=True)
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True)

    class Meta:
        indexes = [models.Index(fields=["site", "state"])]
        constraints = [models.UniqueConstraint(fields=["opportunity"], condition=Q(state="pending"),
                                                name="crm_one_pending_customer_request")]

    @property
    def number(self):
        return f"CR-{self.pk:06d}"


class QuotationLink(models.Model):
    opportunity = models.OneToOneField(Opportunity, on_delete=PROTECT, related_name="quotation_link")
    document = models.OneToOneField("sales.Document", on_delete=PROTECT, related_name="crm_quotation_link")
    actor = models.ForeignKey(USER, on_delete=PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)


class CustomerNumber(models.Model):
    next_number = models.PositiveBigIntegerField(default=1)
