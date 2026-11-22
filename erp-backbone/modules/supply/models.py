from django.conf import settings
from django.db import models

FK = models.ForeignKey
PROTECT = models.PROTECT
Q = models.Q


class Transfer(models.Model):
    site = FK("backbone.Site", on_delete=PROTECT, related_name="+")
    destination = FK("backbone.Site", on_delete=PROTECT, related_name="+")
    note = models.CharField(max_length=500, blank=True)
    state = models.CharField(max_length=16, default="draft")
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def number(self):
        return f"TR-{self.pk:06d}"


class TransferLine(models.Model):
    transfer = FK(Transfer, on_delete=PROTECT, related_name="lines")
    item = FK("inventory.Item", on_delete=PROTECT)
    source = FK("inventory.Location", on_delete=PROTECT, related_name="+")
    destination = FK("inventory.Location", on_delete=PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=28, decimal_places=9)
    dispatched = models.DecimalField(max_digits=28, decimal_places=9, default=0)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0, dispatched__gte=0, dispatched__lte=models.F("quantity")),
                                   name="supply_line_quantity"),
            models.CheckConstraint(condition=~Q(source=models.F("destination")), name="supply_distinct_locations"),
        ]


class Reservation(models.Model):
    site = FK("backbone.Site", on_delete=PROTECT)
    item = FK("inventory.Item", on_delete=PROTECT)
    location = FK("inventory.Location", on_delete=PROTECT)
    transfer_line = models.OneToOneField(TransferLine, on_delete=PROTECT, null=True, related_name="reservation")
    order = FK("sales.Document", on_delete=PROTECT, null=True)
    order_line = models.PositiveIntegerField(null=True)
    owner_key = models.CharField(max_length=100, null=True)
    quantity = models.DecimalField(max_digits=28, decimal_places=9)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gte=0), name="supply_reserved_nonnegative"),
            models.CheckConstraint(condition=Q(transfer_line__isnull=False, order__isnull=True, order_line__isnull=True, owner_key__isnull=True) |
                                   Q(transfer_line__isnull=True, order__isnull=False, order_line__isnull=False, owner_key__isnull=True) |
                                   Q(transfer_line__isnull=True, order__isnull=True, order_line__isnull=True, owner_key__isnull=False),
                                   name="supply_reservation_source"),
            models.UniqueConstraint(fields=["owner_key", "item", "location"], condition=Q(owner_key__isnull=False), name="supply_extension_reservation"),
            models.UniqueConstraint(fields=["order", "order_line", "location"], condition=Q(order__isnull=False),
                                    name="supply_one_sales_reservation"),
        ]
        indexes = [models.Index(fields=["item", "location"], condition=Q(quantity__gt=0), name="supply_reservation_avail")]


class Shipment(models.Model):
    transfer = FK(Transfer, on_delete=PROTECT, related_name="shipments")
    carrier = models.CharField(max_length=120, blank=True)
    reference = models.CharField(max_length=120, blank=True)
    eta = models.DateField(null=True)
    dispatched_at = models.DateTimeField(auto_now_add=True)
    actor = FK(settings.AUTH_USER_MODEL, on_delete=PROTECT)

    @property
    def number(self):
        return f"SHP-{self.pk:06d}"


class ShipmentLine(models.Model):
    shipment = FK(Shipment, on_delete=PROTECT, related_name="lines")
    line = FK(TransferLine, on_delete=PROTECT)
    dispatch = models.OneToOneField("inventory.Movement", on_delete=PROTECT)
    quantity = models.DecimalField(max_digits=28, decimal_places=9)
    value = models.DecimalField(max_digits=26, decimal_places=2)
    remaining = models.DecimalField(max_digits=28, decimal_places=9)
    remaining_value = models.DecimalField(max_digits=26, decimal_places=2)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0, remaining__gte=0, remaining__lte=models.F("quantity"),
                                               value__gte=0, remaining_value__gte=0, remaining_value__lte=models.F("value")),
                                   name="supply_shipment_remaining"),
            models.UniqueConstraint(fields=["shipment", "line"], name="supply_unique_shipment_line"),
        ]


class Discrepancy(models.Model):
    line = FK(ShipmentLine, on_delete=PROTECT, related_name="discrepancies")
    quantity = models.DecimalField(max_digits=28, decimal_places=9)
    kind = models.CharField(max_length=12)
    reason = models.CharField(max_length=500)
    state = models.CharField(max_length=12, default="pending")
    reporter = FK(settings.AUTH_USER_MODEL, on_delete=PROTECT, related_name="+")
    reviewer = FK(settings.AUTH_USER_MODEL, on_delete=PROTECT, related_name="+", null=True)
    decision_reason = models.CharField(max_length=500, blank=True)
    movement = models.OneToOneField("inventory.Movement", on_delete=PROTECT, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0, kind__in=["damage", "shortage"],
                                               state__in=["pending", "approved", "dismissed"]), name="supply_discrepancy_valid"),
            models.CheckConstraint(condition=~Q(reporter=models.F("reviewer")), name="supply_distinct_approver"),
        ]
