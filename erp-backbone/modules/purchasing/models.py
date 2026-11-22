from django.conf import settings
from django.db import models


class Template(models.Model):
    currency = models.CharField(max_length=3)
    version = models.PositiveIntegerField()
    threshold = models.DecimalField(max_digits=17, decimal_places=2)
    steps = models.JSONField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["currency", "version"], name="purchase_template_version")]


class Requisition(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        IN_REVIEW = "in_review", "In review"
        APPROVED = "approved", "Approved"

    number = models.CharField(max_length=60, unique=True, null=True)
    title = models.CharField(max_length=120)
    site = models.ForeignKey("backbone.Site", on_delete=models.PROTECT)
    currency = models.CharField(max_length=3)
    lines = models.JSONField()
    total = models.DecimalField(max_digits=17, decimal_places=2)
    status = models.CharField(max_length=12, choices=Status, default=Status.DRAFT)
    revision = models.PositiveIntegerField(default=1)


    @property
    def document_number(self):
        return self.number or f"PR-{self.pk}"


class Review(models.Model):
    requisition = models.ForeignKey(Requisition, on_delete=models.PROTECT, related_name="reviews")
    template = models.ForeignKey(Template, on_delete=models.PROTECT)
    submitter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    submitted_at = models.DateTimeField(auto_now_add=True)
    steps = models.JSONField()
    total = models.DecimalField(max_digits=17, decimal_places=2)
    next_step = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(max_length=12, default="pending")
    decisions = models.JSONField(default=list)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["requisition"], condition=models.Q(status="pending"),
                                               name="purchase_one_pending_review")]


class Supplier(models.Model):
    party = models.ForeignKey("parties.Party", on_delete=models.PROTECT, null=True, blank=True)

    def save(self,*args,**kwargs):
        if self._state.adding and self.party_id is None:
            from parties.models import Party
            from parties.matching import match
            self.party_id = match(self,Party.objects)
        return super().save(*args,**kwargs)

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=120)
    legal_name = models.CharField(max_length=160, blank=True)
    currency_default = models.CharField(max_length=3)
    contact = models.CharField(max_length=240, blank=True)
    tax_id = models.CharField(max_length=60, blank=True)
    active = models.BooleanField(default=True)
    revision = models.PositiveIntegerField(default=1)


class PurchaseOrder(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ISSUED = "issued", "Issued"
        CANCELLED = "cancelled", "Cancelled"

    number = models.CharField(max_length=60, unique=True)
    source_pr = models.OneToOneField(Requisition, on_delete=models.PROTECT, related_name="purchase_order")
    site = models.ForeignKey("backbone.Site", on_delete=models.PROTECT)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT)
    title = models.CharField(max_length=120)
    currency = models.CharField(max_length=3)
    lines = models.JSONField()
    total = models.DecimalField(max_digits=17, decimal_places=2)
    price_changes = models.JSONField(default=list)
    status = models.CharField(max_length=12, choices=Status, default=Status.DRAFT)
    revision = models.PositiveIntegerField(default=1)
    issued_at = models.DateTimeField(null=True)
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, related_name="+")
    snapshot = models.JSONField(null=True)
    void_reason = models.CharField(max_length=1000, blank=True)
    voided_at = models.DateTimeField(null=True)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, related_name="+")

    @property
    def document_number(self):
        return self.number

    class Meta:
        indexes = [
            models.Index(fields=['status', 'site', 'supplier'], name='rpt_po_status_party'),
            models.Index(fields=['supplier', 'status', 'site'], name='rpt_po_party_status'),
        ]


class PurchaseReceiptLink(models.Model):
    """Records confirmed receipt references for the PO lifecycle guard."""

    order = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name="receipt_links")
    receipt_reference = models.CharField(max_length=100, unique=True)
