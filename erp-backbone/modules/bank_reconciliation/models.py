from django.db import models

PROTECT = models.PROTECT


class BankProfile(models.Model):
    site = models.ForeignKey("backbone.Site", on_delete=PROTECT)
    account = models.ForeignKey("accounting.Account", on_delete=PROTECT)
    charges = models.ForeignKey("accounting.Account", on_delete=PROTECT, related_name="+")
    interest = models.ForeignKey("accounting.Account", on_delete=PROTECT, related_name="+")
    name = models.CharField(max_length=100)
    last_four = models.CharField(max_length=4)
    currency = models.CharField(max_length=3)
    tolerance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    last_balance = models.DecimalField(max_digits=24, decimal_places=2, null=True)
    last_date = models.DateField(null=True)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["site", "account"], name="bank_profile_per_site_gl")]


class BankStatement(models.Model):
    profile = models.ForeignKey(BankProfile, on_delete=PROTECT)
    start = models.DateField()
    end = models.DateField()
    opening_balance = models.DecimalField(max_digits=24, decimal_places=2)
    statement_ending_balance = models.DecimalField(max_digits=24, decimal_places=2)
    status = models.CharField(max_length=16, default="draft")
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["profile"], condition=~models.Q(status="reconciled"), name="bank_one_open_statement")]


class BankMatch(models.Model):
    statement = models.ForeignKey(BankStatement, on_delete=PROTECT)
    actor = models.ForeignKey("auth.User", on_delete=PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)


class BankStatementLine(models.Model):
    statement = models.ForeignKey(BankStatement, on_delete=PROTECT, related_name="lines")
    date = models.DateField()
    description = models.CharField(max_length=300)
    reference = models.CharField(max_length=150, blank=True)
    amount = models.DecimalField(max_digits=22, decimal_places=2)
    running_balance = models.DecimalField(max_digits=24, decimal_places=2, null=True)
    match = models.ForeignKey(BankMatch, null=True, on_delete=PROTECT)


class ClearedLine(models.Model):
    match = models.ForeignKey(BankMatch, on_delete=PROTECT)
    line = models.OneToOneField("accounting.Line", on_delete=PROTECT)


class StatementImport(models.Model):
    statement = models.ForeignKey(BankStatement, on_delete=PROTECT)
    digest = models.CharField(max_length=64)
    attachment = models.ForeignKey("media_service.InternalReceipt", null=True, on_delete=PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["statement","digest"], name="bank_unique_csv_import")]
