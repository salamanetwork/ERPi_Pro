from datetime import time

from django.db import models


class Unit(models.Model):
    code = models.CharField(max_length=24, unique=True)
    name = models.CharField(max_length=80)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)


class Item(models.Model):
    code = models.CharField(max_length=32, unique=True)
    barcode = models.CharField(max_length=64, null=True, blank=True, db_index=True)
    name = models.CharField(max_length=120)
    default_price = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    base_unit = models.ForeignKey(Unit, on_delete=models.PROTECT)
    active = models.BooleanField(default=True)
    revision = models.PositiveIntegerField(default=1)


class Conversion(models.Model):
    item = models.ForeignKey(Item, on_delete=models.PROTECT, null=True)
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="+")
    base_unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="+")
    factor = models.DecimalField(max_digits=18, decimal_places=9)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["item", "unit", "base_unit"], condition=models.Q(item__isnull=False),
                                    name="inventory_item_conversion"),
            models.UniqueConstraint(fields=["unit", "base_unit"], condition=models.Q(item__isnull=True),
                                    name="inventory_general_conversion"),
        ]


class Warehouse(models.Model):
    active = models.BooleanField(default=True)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    site = models.ForeignKey("backbone.Site", on_delete=models.PROTECT)
    code = models.CharField(max_length=32)
    name = models.CharField(max_length=120)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["site", "code"], name="inventory_warehouse_code")]


class Location(models.Model):
    active = models.BooleanField(default=True)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT)
    code = models.CharField(max_length=32)
    name = models.CharField(max_length=120)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["warehouse", "code"], name="inventory_location_code")]


class SiteSettings(models.Model):
    site = models.OneToOneField("backbone.Site", on_delete=models.PROTECT)
    timezone = models.CharField(max_length=80, default="UTC")
    day_start = models.TimeField(default=time(0))
    opening_closed_at = models.DateTimeField(null=True)


class Position(models.Model):
    site = models.ForeignKey("backbone.Site", on_delete=models.PROTECT)
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    location = models.ForeignKey(Location, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=28, decimal_places=9, default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["item", "location"], name="inventory_item_position")]


class Valuation(models.Model):
    site = models.ForeignKey("backbone.Site", on_delete=models.PROTECT)
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=28, decimal_places=9, default=0)
    value = models.DecimalField(max_digits=26, decimal_places=2, default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["site", "item"], name="inventory_site_valuation")]


class OrderLine(models.Model):
    order = models.ForeignKey("purchasing.PurchaseOrder", on_delete=models.PROTECT)
    line_id = models.PositiveIntegerField()
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT)
    factor = models.DecimalField(max_digits=18, decimal_places=9)
    received = models.DecimalField(max_digits=18, decimal_places=3, default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["order", "line_id"], name="inventory_order_line")]


class Receipt(models.Model):
    number = models.CharField(max_length=60, unique=True)
    site = models.ForeignKey("backbone.Site", on_delete=models.PROTECT)
    order = models.ForeignKey("purchasing.PurchaseOrder", on_delete=models.PROTECT)
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT)
    supplier_reference = models.CharField(max_length=120, blank=True)
    lines = models.JSONField()
    status = models.CharField(max_length=12, default="draft")
    revision = models.PositiveIntegerField(default=1)
    confirmed_at = models.DateTimeField(null=True)
    confirmed_by = models.CharField(max_length=150, blank=True)

    @property
    def document_number(self):
        return self.number


class Movement(models.Model):
    site = models.ForeignKey("backbone.Site", on_delete=models.PROTECT)
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    location = models.ForeignKey(Location, on_delete=models.PROTECT)
    kind = models.CharField(max_length=16)
    quantity = models.DecimalField(max_digits=28, decimal_places=9)
    value = models.DecimalField(max_digits=26, decimal_places=2)
    receipt = models.ForeignKey(Receipt, on_delete=models.PROTECT, null=True)
    original = models.ForeignKey("self", on_delete=models.PROTECT, null=True, related_name="reversals")
    actor = models.CharField(max_length=150)
    actor_id = models.PositiveBigIntegerField()
    occurred_at = models.DateTimeField()
    business_date = models.DateField()
    facts = models.JSONField()

    class Meta:
        indexes = [
            models.Index(fields=['kind', 'site', 'business_date'], name='rpt_move_site_date'),
            models.Index(fields=['original', 'kind', 'business_date'], name='rpt_move_original_date'),
        ]
