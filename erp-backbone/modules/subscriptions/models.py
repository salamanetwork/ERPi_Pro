from django.db import models
from backbone.business_scope import ScopedRecord

PROTECT = models.PROTECT

class Plan(ScopedRecord):
    code = models.CharField(max_length=40)
    name_en = models.CharField(max_length=120)
    name_ar = models.CharField(max_length=120)
    active = models.BooleanField(default=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=('site','code'), name='subscription_plan_code')]

class PlanVersion(models.Model):
    plan = models.ForeignKey(Plan, on_delete=PROTECT)
    version = models.PositiveIntegerField()
    currency = models.CharField(max_length=3)
    interval = models.CharField(max_length=10)
    days = models.PositiveIntegerField(default=1)
    price = models.DecimalField(max_digits=14, decimal_places=2)
    setup = models.DecimalField(max_digits=14, decimal_places=2)
    tax = models.DecimalField(max_digits=6, decimal_places=3)
    trial_days = models.PositiveIntegerField(default=0)
    class Meta:
        unique_together = [('plan','version')]

class Contract(ScopedRecord):
    party = models.ForeignKey('parties.Party', on_delete=PROTECT)
    version = models.ForeignKey(PlanVersion, on_delete=PROTECT)
    renewal_version = models.ForeignKey(PlanVersion, on_delete=PROTECT, null=True, related_name="+")
    start = models.DateField()
    next_date = models.DateField(db_index=True)
    anchor = models.DateField()
    zone = models.CharField(max_length=80)
    status = models.CharField(max_length=12)
    cancel_on = models.DateField(null=True)
    next_check = models.DateTimeField(null=True, db_index=True)

class Cycle(models.Model):
    contract = models.ForeignKey(Contract, on_delete=PROTECT)
    version = models.ForeignKey(PlanVersion, on_delete=PROTECT)
    start = models.DateField()
    end = models.DateField()
    due = models.DateField()
    invoice = models.OneToOneField('billing_core.Invoice', null=True, on_delete=PROTECT)
    credit_used = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    class Meta:
        unique_together = [('contract','start')]

class Adjustment(models.Model):
    contract = models.ForeignKey(Contract, on_delete=PROTECT)
    version = models.ForeignKey(PlanVersion, on_delete=PROTECT)
    effective = models.DateField()
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    remaining = models.DecimalField(max_digits=18, decimal_places=2)
    tax = models.DecimalField(max_digits=6, decimal_places=3)

class Allocation(models.Model):
    cycle = models.ForeignKey(Cycle, on_delete=PROTECT)
    adjustment = models.ForeignKey(Adjustment, on_delete=PROTECT)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    class Meta:
        unique_together = [('cycle','adjustment')]

class DunningPolicy(models.Model):
    site = models.OneToOneField('backbone.Site', on_delete=PROTECT)
    first = models.PositiveIntegerField(default=1)
    second = models.PositiveIntegerField(default=7)
    final = models.PositiveIntegerField(default=14)
    suspend = models.PositiveIntegerField(default=21)
    revision = models.PositiveIntegerField(default=1)

class Notice(models.Model):
    cycle = models.ForeignKey(Cycle, on_delete=PROTECT)
    stage = models.PositiveIntegerField()
    delivered_at = models.DateTimeField(null=True)
    retry_at = models.DateTimeField(null=True, db_index=True)
    class Meta:
        unique_together = [('cycle','stage')]

class CancellationRequest(models.Model):
    contract = models.ForeignKey(Contract, on_delete=PROTECT)
    identity_id = models.UUIDField()
    key = models.UUIDField()
    reason = models.CharField(max_length=500)
    status = models.CharField(max_length=12, default='pending')
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['identity_id','key'], name='subscription_cancel_request_key'),
            models.UniqueConstraint(fields=['contract'], condition=models.Q(status='pending'),name='subscription_cancel_pending')]

class SubscriptionGLMapping(models.Model):
    site = models.OneToOneField('backbone.Site', on_delete=models.PROTECT)
    revision = models.PositiveIntegerField(default=0)
    def account():
        return models.ForeignKey('accounting.Account', on_delete=models.PROTECT, related_name='+')
    receivable = account()
    deferred = account()
    revenue = account()
    setup = account()
    vat = account()
    bank = account()


class SubscriptionRevenueSchedule(models.Model):
    cycle = models.OneToOneField(Cycle, on_delete=models.PROTECT, related_name='schedule')
    last_cutoff = models.DateField(null=True)
    recognized = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    warning = models.CharField(max_length=80, blank=True)
    complete = models.BooleanField(default=False)
    next_check = models.DateTimeField(null=True, db_index=True)


class SubscriptionGLPosting(models.Model):
    key = models.CharField(max_length=120, unique=True)
    cycle = models.ForeignKey(Cycle, on_delete=models.PROTECT)
    entry = models.OneToOneField('accounting.Entry', on_delete=models.PROTECT)
