from django.conf import settings
from django.db import models

PROTECT = models.PROTECT
class ExpenseClaim(models.Model):
    employee = models.ForeignKey("hr.Employee", on_delete=PROTECT)
    site = models.ForeignKey("backbone.Site", on_delete=PROTECT)
    project = models.ForeignKey("projects.Project", null=True, on_delete=PROTECT)
    cost_center = models.ForeignKey("accounting.Reference", null=True, on_delete=PROTECT)
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=PROTECT)
    currency = models.CharField(max_length=3)
    method = models.CharField(max_length=20)
    status = models.CharField(max_length=24, default="draft")
    revision = models.PositiveIntegerField(default=1)
    gross = models.DecimalField(max_digits=18, decimal_places=2)
    amount_paid = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    review_note = models.CharField(max_length=500, blank=True)
    entry = models.OneToOneField("accounting.Entry", null=True, on_delete=PROTECT)

    @property
    def balance_due(self):
        return self.gross-self.amount_paid if self.method=="out_of_pocket" else 0

class ExpenseLine(models.Model):
    claim = models.ForeignKey(ExpenseClaim, related_name="lines", on_delete=PROTECT)
    category = models.CharField(max_length=30)
    date = models.DateField()
    description = models.CharField(max_length=240)
    net = models.DecimalField(max_digits=16, decimal_places=2)
    tax = models.DecimalField(max_digits=16, decimal_places=2)
    recoverable = models.BooleanField(default=False)
    receipt = models.OneToOneField("media_service.InternalReceipt", null=True, on_delete=PROTECT)

class Settlement(models.Model):
    claim = models.ForeignKey(ExpenseClaim, related_name="settlements", on_delete=PROTECT)
    date = models.DateField()
    reference = models.CharField(max_length=120)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    entry = models.OneToOneField("accounting.Entry", on_delete=PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=PROTECT)
