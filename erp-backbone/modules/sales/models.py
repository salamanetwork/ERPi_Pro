from django.db import models


class Customer(models.Model):
    party = models.ForeignKey("parties.Party", on_delete=models.PROTECT, null=True, blank=True)

    def save(self,*args,**kwargs):
        if self._state.adding and self.party_id is None:
            from parties.models import Party
            from parties.matching import match
            self.party_id = match(self,Party.objects)
        return super().save(*args,**kwargs)

    code = models.CharField(max_length=24, unique=True)
    name = models.CharField(max_length=120)
    legal_name = models.CharField(max_length=120, blank=True)
    contact = models.CharField(max_length=120, blank=True)
    billing = models.CharField(max_length=240, blank=True)
    shipping = models.CharField(max_length=240, blank=True)
    tax_id = models.CharField(max_length=60, blank=True)
    terms = models.PositiveSmallIntegerField(default=30)
    active = models.BooleanField(default=True)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    revision = models.PositiveIntegerField(default=1)


class Document(models.Model):
    kind = models.CharField(max_length=12)
    site = models.ForeignKey("backbone.Site", on_delete=models.PROTECT)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT)
    source = models.ForeignKey("self", on_delete=models.PROTECT, null=True, related_name="children")
    order = models.ForeignKey("self", on_delete=models.PROTECT, null=True, related_name="documents")
    date = models.DateField()
    expires = models.DateField(null=True)
    due = models.DateField(null=True)
    status = models.CharField(max_length=16)
    revision = models.PositiveIntegerField(default=1)
    lines = models.JSONField(default=list)
    snapshot = models.JSONField(default=dict)
    amount = models.DecimalField(max_digits=22, decimal_places=2, default=0)
    reason = models.CharField(max_length=1000, blank=True)
    actor = models.CharField(max_length=150)

    class Meta:
        indexes = [
            models.Index(fields=['kind', 'site', 'date'], name='rpt_sale_site_date'),
            models.Index(fields=['customer', 'kind', 'date'], name='rpt_sale_party_date'),
            models.Index(fields=['site', 'kind', 'due'], name='rpt_sale_site_due'),
            models.Index(fields=["date", "site"], condition=models.Q(kind="receipt"), name="rpt_receipt_date_site"),
        ]
        constraints = [models.UniqueConstraint(fields=["source"], condition=models.Q(kind="order"),
                                               name="sales_one_order_per_quote")]

    @property
    def number(self):
        prefix = {"quote": "QT", "order": "SO", "delivery": "DN", "invoice": "SI",
                  "receipt": "SR", "return": "RT", "credit": "CN"}
        return f"{prefix[self.kind]}-{self.pk:06d}"


class PostingEvent(models.Model):
    document = models.OneToOneField(Document, on_delete=models.PROTECT)
    prerequisites = models.ManyToManyField("self", symmetrical=False, blank=True)
    # Immutable economic amounts are stored on the document and its lines.
