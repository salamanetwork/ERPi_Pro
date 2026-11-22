from django.conf import settings
from django.db import models

FK = models.ForeignKey
PROTECT = models.PROTECT
Q = models.Q


class Bill(models.Model):
    site = FK("backbone.Site", on_delete=PROTECT)
    item = FK("inventory.Item", on_delete=PROTECT)
    version = models.PositiveIntegerField()
    effective = models.DateField()
    quantity = models.DecimalField(max_digits=28, decimal_places=9)
    components = models.JSONField()
    state = models.CharField(max_length=12, default="draft")
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["site", "item", "version"], name="mfg_bill_version"),
            models.UniqueConstraint(fields=["site", "item", "effective"], condition=Q(state="released"), name="mfg_bill_effective"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="mfg_bill_quantity"),
        ]


class Order(models.Model):
    site = FK("backbone.Site", on_delete=PROTECT)
    item = FK("inventory.Item", on_delete=PROTECT)
    source = FK("inventory.Location", on_delete=PROTECT, related_name="+")
    destination = FK("inventory.Location", on_delete=PROTECT, related_name="+")
    bill = FK(Bill, on_delete=PROTECT, null=True)
    due = models.DateField()
    quantity = models.DecimalField(max_digits=28, decimal_places=9)
    state = models.CharField(max_length=12, default="draft")
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["site", "state", "due"], name="mfg_order_planning")]

    @property
    def number(self):
        return f"MO-{self.pk:06d}"


class Material(models.Model):
    order = FK(Order, on_delete=PROTECT, related_name="materials")
    item = FK("inventory.Item", on_delete=PROTECT)
    source = FK("inventory.Location", on_delete=PROTECT, related_name="+")
    required = models.DecimalField(max_digits=28, decimal_places=9)
    snapshot = models.JSONField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["order", "item"], name="mfg_order_material")]


class Batch(models.Model):
    order = FK(Order, on_delete=PROTECT, related_name="batches")
    quantity = models.DecimalField(max_digits=28, decimal_places=9)
    received_by = FK(settings.AUTH_USER_MODEL, on_delete=PROTECT)
    received_at = models.DateTimeField(auto_now_add=True)
    movement = models.OneToOneField("inventory.Movement", on_delete=PROTECT, null=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(quantity__gt=0), name="mfg_batch_quantity")]


class Activity(models.Model):
    order = FK(Order, on_delete=PROTECT, related_name="activities")
    material = FK(Material, on_delete=PROTECT, null=True)
    movement = models.OneToOneField("inventory.Movement", on_delete=PROTECT)
    original = FK("self", on_delete=PROTECT, null=True, related_name="followups")
    action = models.CharField(max_length=12)
    quantity = models.DecimalField(max_digits=28, decimal_places=9)
    value = models.DecimalField(max_digits=26, decimal_places=2)
    reason = models.CharField(max_length=500, blank=True)


class ExceptionRequest(models.Model):
    order = FK(Order, on_delete=PROTECT, related_name="exceptions")
    material = FK(Material, on_delete=PROTECT)
    original = FK(Activity, on_delete=PROTECT, null=True)
    kind = models.CharField(max_length=12)
    quantity = models.DecimalField(max_digits=28, decimal_places=9)
    reporter = FK(settings.AUTH_USER_MODEL, on_delete=PROTECT, related_name="+")
    reviewer = FK(settings.AUTH_USER_MODEL, on_delete=PROTECT, null=True, related_name="+")
    state = models.CharField(max_length=12, default="pending")
    reason = models.CharField(max_length=500)
    decision_reason = models.CharField(max_length=500, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=~Q(reporter=models.F("reviewer")), name="mfg_distinct_reviewer")]


class Policy(models.Model):
    site = FK("backbone.Site", on_delete=PROTECT)
    item = FK("inventory.Item", on_delete=PROTECT)
    method = models.CharField(max_length=10)
    lead_days = models.PositiveSmallIntegerField(default=0)
    source = FK("inventory.Location", on_delete=PROTECT, null=True, related_name="+")
    destination = FK("inventory.Location", on_delete=PROTECT, null=True, related_name="+")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["site", "item"], name="mfg_item_policy")]


class Plan(models.Model):
    site = FK("backbone.Site", on_delete=PROTECT)
    start = models.DateField()
    end = models.DateField()
    inputs = models.JSONField()
    created_by = FK(settings.AUTH_USER_MODEL, on_delete=PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)


class Proposal(models.Model):
    plan = FK(Plan, on_delete=PROTECT, related_name="proposals")
    item = FK("inventory.Item", on_delete=PROTECT)
    quantity = models.DecimalField(max_digits=28, decimal_places=9)
    due = models.DateField()
    start = models.DateField()
    method = models.CharField(max_length=10)
    facts = models.JSONField()
    production = models.OneToOneField(Order, on_delete=PROTECT, null=True)
    purchase = models.OneToOneField("purchasing.Requisition", on_delete=PROTECT, null=True)
    transfer = models.OneToOneField("supply.Transfer", on_delete=PROTECT, null=True)
