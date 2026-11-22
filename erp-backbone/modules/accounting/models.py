from django.db import models


class Ledger(models.Model):
    currency = models.CharField(max_length=3, default="AED")
    cutoff = models.DateField(null=True)
    activated_at = models.DateTimeField(null=True)
    activated_by = models.CharField(max_length=150, blank=True)


class Period(models.Model):
    ledger = models.ForeignKey(Ledger, on_delete=models.PROTECT)
    start = models.DateField()
    end = models.DateField()
    closed_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["ledger", "start"], name="finance_period_start")]


class Account(models.Model):
    ledger = models.ForeignKey(Ledger, on_delete=models.PROTECT)
    code = models.CharField(max_length=24)
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=12)
    parent = models.ForeignKey("self", on_delete=models.PROTECT, null=True)
    detail = models.BooleanField()
    control = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["ledger", "code"], name="finance_account_code")]


class Rule(models.Model):
    ledger = models.ForeignKey(Ledger, on_delete=models.PROTECT)
    kind = models.CharField(max_length=24)
    starts = models.DateField()
    inventory = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+")
    offset = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+")
    variance = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+", null=True)
    revenue = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+", null=True)
    created_by = models.CharField(max_length=150)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["ledger", "kind", "starts"], name="finance_rule_version")]


class Reference(models.Model):
    ledger = models.ForeignKey(Ledger, on_delete=models.PROTECT)
    kind = models.CharField(max_length=12)
    code = models.CharField(max_length=24)
    name = models.CharField(max_length=120)
    rate = models.DecimalField(max_digits=7, decimal_places=4, null=True)
    starts = models.DateField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["ledger", "kind", "code", "starts"],
                        condition=models.Q(starts__isnull=False), name="finance_reference_version"),
            models.UniqueConstraint(fields=["ledger", "kind", "code"],
                condition=models.Q(starts__isnull=True), name="finance_reference_code")]


class Entry(models.Model):
    ledger = models.ForeignKey(Ledger, on_delete=models.PROTECT)
    period = models.ForeignKey(Period, on_delete=models.PROTECT, null=True)
    date = models.DateField()
    source_date = models.DateField(null=True)
    description = models.TextField()
    kind = models.CharField(max_length=16)
    created_by = models.CharField(max_length=150)
    posted_by = models.CharField(max_length=150, blank=True)
    posted_at = models.DateTimeField(null=True)
    posting_note = models.CharField(max_length=1000, blank=True)
    revision = models.PositiveIntegerField(default=1)
    rule = models.ForeignKey(Rule, on_delete=models.PROTECT, null=True)
    reversal_of = models.OneToOneField("self", on_delete=models.PROTECT, null=True, related_name="reversed_by")
    original_entry = models.ForeignKey("self", on_delete=models.PROTECT, null=True, related_name="stock_reversals")

    @property
    def number(self):
        return f"JE-{self.pk:06d}"

    class Meta:
        indexes = [
            models.Index(fields=['ledger', 'date', 'posted_at'], name='rpt_entry_ledger_date'),
            models.Index(fields=["date", "ledger"], name="rpt_entry_date_ledger"),
        ]


class Line(models.Model):
    entry = models.ForeignKey(Entry, on_delete=models.PROTECT, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=26, decimal_places=2)
    site = models.ForeignKey("backbone.Site", on_delete=models.PROTECT, null=True)
    cost_center = models.ForeignKey(Reference, on_delete=models.PROTECT, null=True)

    class Meta:
        indexes = [
            models.Index(fields=['account', 'site', 'entry'], name='rpt_line_account_site'),
        ]
        constraints = [models.CheckConstraint(condition=~models.Q(amount=0), name="finance_line_nonzero")]


class Posting(models.Model):
    ledger = models.ForeignKey(Ledger, on_delete=models.PROTECT)
    movement = models.OneToOneField("inventory.Movement", on_delete=models.PROTECT, null=True)
    sale = models.OneToOneField("sales.PostingEvent", on_delete=models.PROTECT, null=True)
    entry = models.OneToOneField(Entry, on_delete=models.PROTECT, null=True)
    state = models.CharField(max_length=12, default="pending")
    error = models.CharField(max_length=80, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['ledger', 'state', 'id'], name='rpt_posting_state'),
        ]
        constraints = [models.CheckConstraint(condition=(
            models.Q(movement__isnull=False, sale__isnull=True) | models.Q(movement__isnull=True, sale__isnull=False)),
            name="finance_posting_one_source")]


class Accrual(models.Model):
    ledger = models.ForeignKey(Ledger, on_delete=models.PROTECT)
    receipt_movement = models.OneToOneField("inventory.Movement", on_delete=models.PROTECT)
    account = models.ForeignKey(Account, on_delete=models.PROTECT)
    inventory_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+")
    entry = models.ForeignKey(Entry, on_delete=models.PROTECT, null=True)
    remaining = models.DecimalField(max_digits=26, decimal_places=2)


class SupplierInvoice(models.Model):
    order = models.ForeignKey("purchasing.PurchaseOrder", on_delete=models.PROTECT)
    supplier = models.ForeignKey("purchasing.Supplier", on_delete=models.PROTECT)
    site = models.ForeignKey("backbone.Site", on_delete=models.PROTECT)
    supplier_number = models.CharField(max_length=100)
    supplier_number_key = models.CharField(max_length=300)
    invoice_date = models.DateField()
    due_date = models.DateField()
    created_by = models.ForeignKey("auth.User", on_delete=models.PROTECT)
    status = models.CharField(max_length=12, default="draft")
    revision = models.PositiveIntegerField(default=1)
    draft_lines = models.JSONField()
    declared_total = models.DecimalField(max_digits=26, decimal_places=2)
    snapshot = models.JSONField(default=dict)
    total = models.DecimalField(max_digits=26, decimal_places=2, default=0)
    entry = models.OneToOneField(Entry, on_delete=models.PROTECT, null=True, related_name="supplier_invoice")
    reversal_entry = models.OneToOneField(Entry, on_delete=models.PROTECT, null=True, related_name="ap_invoice_reversal")

    @property
    def document_number(self):
        return f"SI-{self.pk:06d}"

    class Meta:
        indexes = [
            models.Index(fields=['site', 'supplier', 'due_date'], name='rpt_ap_party_due'),
            models.Index(fields=['supplier', 'site', 'entry'], name='rpt_ap_party_entry'),
        ]
        constraints = [models.UniqueConstraint(fields=["supplier", "supplier_number_key"],
                                                name="ap_supplier_invoice_number")]


class InvoiceMatch(models.Model):
    invoice = models.ForeignKey(SupplierInvoice, on_delete=models.PROTECT, related_name="matches")
    accrual = models.ForeignKey(Accrual, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=18, decimal_places=3)
    amount = models.DecimalField(max_digits=26, decimal_places=2)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["invoice", "accrual"], name="ap_invoice_receipt_once")]


class SupplierSettlement(models.Model):
    supplier = models.ForeignKey("purchasing.Supplier", on_delete=models.PROTECT)
    site = models.ForeignKey("backbone.Site", on_delete=models.PROTECT)
    kind = models.CharField(max_length=12)
    reference = models.CharField(max_length=100)
    reason = models.CharField(max_length=1000, blank=True)
    entry = models.OneToOneField(Entry, on_delete=models.PROTECT, related_name="supplier_settlement")
    total = models.DecimalField(max_digits=26, decimal_places=2)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["supplier", "kind", "reference"],
                                                name="ap_supplier_settlement_reference")]


class SettlementAllocation(models.Model):
    settlement = models.ForeignKey(SupplierSettlement, on_delete=models.PROTECT, related_name="allocations")
    invoice = models.ForeignKey(SupplierInvoice, on_delete=models.PROTECT, related_name="allocations")
    amount = models.DecimalField(max_digits=26, decimal_places=2)
    return_movement = models.ForeignKey("inventory.Movement", on_delete=models.PROTECT, null=True)
    match = models.ForeignKey(InvoiceMatch, on_delete=models.PROTECT, null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["return_movement", "match"],
            condition=models.Q(return_movement__isnull=False), name="ap_return_credit_once")]


class CostCenterScope(models.Model):
    reference = models.OneToOneField(Reference, on_delete=models.PROTECT, related_name="center_scope")
    site = models.ForeignKey("backbone.Site", on_delete=models.PROTECT)
    active = models.BooleanField(default=True)
    revision = models.PositiveIntegerField(default=1)


class BudgetVersion(models.Model):
    site = models.ForeignKey("backbone.Site", on_delete=models.PROTECT)
    center = models.ForeignKey(Reference, on_delete=models.PROTECT)
    account = models.ForeignKey(Account, on_delete=models.PROTECT)
    year = models.PositiveSmallIntegerField()
    version = models.PositiveIntegerField()
    state = models.CharField(max_length=12, default="draft")
    current = models.BooleanField(default=False)
    threshold = models.PositiveSmallIntegerField(default=80)
    reason = models.CharField(max_length=1000, blank=True)
    created_by = models.ForeignKey("auth.User", on_delete=models.PROTECT)
    approved_by = models.ForeignKey("auth.User", on_delete=models.PROTECT, null=True, related_name="+")
    approved_at = models.DateTimeField(null=True)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["site", "center", "account", "year", "version"], name="budget_version_unique"),
            models.UniqueConstraint(fields=["site", "center", "account", "year"], condition=models.Q(current=True),
                                    name="budget_one_current"),
        ]


class BudgetMonth(models.Model):
    budget = models.ForeignKey(BudgetVersion, on_delete=models.PROTECT, related_name="months")
    site = models.ForeignKey("backbone.Site", on_delete=models.PROTECT)
    center = models.ForeignKey(Reference, on_delete=models.PROTECT)
    account = models.ForeignKey(Account, on_delete=models.PROTECT)
    year = models.PositiveSmallIntegerField()
    version = models.PositiveIntegerField()
    month = models.PositiveSmallIntegerField()
    amount = models.DecimalField(max_digits=17, decimal_places=2)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["site", "center", "account", "month", "year", "version"],
                                    name="budget_month_dimensions_unique"),
            models.UniqueConstraint(fields=["budget", "month"], name="budget_header_month_unique"),
            models.CheckConstraint(condition=models.Q(month__gte=1, month__lte=12, amount__gte=0),
                                   name="budget_valid_month_amount"),
        ]
