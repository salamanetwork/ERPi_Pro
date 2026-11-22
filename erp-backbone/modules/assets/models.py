from django.db import models


class Asset(models.Model):
    name = models.CharField(max_length=120)
    tag = models.CharField(max_length=60, unique=True)
    revision = models.PositiveIntegerField(default=1)
    purchased_on = models.DateField(null=True, blank=True)
    purchase_cost = models.DecimalField(max_digits=17, decimal_places=2, null=True, blank=True)
    purchase_currency = models.CharField(max_length=3, blank=True)
    purchase_reference = models.CharField(max_length=120, blank=True)
    site = models.ForeignKey("backbone.Site", on_delete=models.PROTECT, null=True)


class AssetProfile(models.Model):
    asset = models.OneToOneField(Asset, on_delete=models.PROTECT, related_name="maintenance_profile")
    equipment_type = models.CharField(max_length=100, blank=True)
    functional_location = models.CharField(max_length=120, blank=True)
    state = models.CharField(max_length=12, default="active")
    parent = models.ForeignKey(Asset, on_delete=models.PROTECT, null=True, related_name="maintenance_children")
    spare_parts = models.JSONField(default=list)
    revision = models.PositiveIntegerField(default=1)


class AssetTransfer(models.Model):
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="transfers")
    source_site = models.ForeignKey("backbone.Site", on_delete=models.PROTECT, null=True, related_name="+")
    destination_site = models.ForeignKey("backbone.Site", on_delete=models.PROTECT, related_name="+")
    transferred_on = models.DateField()
    recorded_at = models.DateTimeField(auto_now_add=True)
    reason = models.CharField(max_length=500)
    actor = models.ForeignKey("auth.User", on_delete=models.PROTECT)
