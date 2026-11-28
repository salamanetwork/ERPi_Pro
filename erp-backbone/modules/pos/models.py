from decimal import Decimal
from django.conf import settings
from django.db import models
from django.utils import timezone


class PosCategory(models.Model):
    site = models.ForeignKey("backbone.Site", on_delete=models.PROTECT)
    code = models.CharField(max_length=32)
    name_en = models.CharField(max_length=120)
    name_ar = models.CharField(max_length=120, blank=True)
    active = models.BooleanField(default=True)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["site", "code"], name="pos_category_site_code")
        ]


class PosTerminal(models.Model):
    site = models.ForeignKey("backbone.Site", on_delete=models.PROTECT)
    code = models.CharField(max_length=32)
    name = models.CharField(max_length=120)
    cash_account = models.ForeignKey("accounting.Account", on_delete=models.PROTECT, related_name="pos_cash_terminals")
    card_account = models.ForeignKey("accounting.Account", on_delete=models.PROTECT, null=True, blank=True, related_name="pos_card_terminals")
    over_short_account = models.ForeignKey("accounting.Account", on_delete=models.PROTECT, null=True, blank=True, related_name="pos_overshort_terminals")
    revenue_account = models.ForeignKey("accounting.Account", on_delete=models.PROTECT, null=True, blank=True, related_name="pos_revenue_terminals")
    tax_account = models.ForeignKey("accounting.Account", on_delete=models.PROTECT, null=True, blank=True, related_name="pos_tax_terminals")
    cogs_account = models.ForeignKey("accounting.Account", on_delete=models.PROTECT, null=True, blank=True, related_name="pos_cogs_terminals")
    inventory_account = models.ForeignKey("accounting.Account", on_delete=models.PROTECT, null=True, blank=True, related_name="pos_inventory_terminals")
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, null=True, blank=True)
    location = models.ForeignKey("inventory.Location", on_delete=models.PROTECT, null=True, blank=True)
    tax_rate = models.DecimalField(max_digits=6, decimal_places=4, default=Decimal("0.0000"))
    is_active = models.BooleanField(default=True)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["site", "code"], name="pos_terminal_site_code")
        ]


class PosShift(models.Model):
    STATUS_OPEN = "open"
    STATUS_CLOSED = "closed"
    STATUS_CHOICES = [(STATUS_OPEN, "Open"), (STATUS_CLOSED, "Closed")]

    terminal = models.ForeignKey(PosTerminal, on_delete=models.PROTECT, related_name="shifts")
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="pos_shifts")
    opened_at = models.DateTimeField(default=timezone.now)
    opening_balance = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    closed_at = models.DateTimeField(null=True, blank=True)
    closing_balance = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    expected_closing = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    cash_variance = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_OPEN)
    notes = models.CharField(max_length=500, blank=True)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        indexes = [
            models.Index(fields=["terminal", "status"], name="pos_shift_term_status"),
        ]


class PosSale(models.Model):
    STATUS_PAID = "paid"
    STATUS_PARTIAL = "partial"
    STATUS_VOID = "void"
    STATUS_REFUNDED = "refunded"
    STATUS_CHOICES = [
        (STATUS_PAID, "Paid"),
        (STATUS_PARTIAL, "Partial"),
        (STATUS_VOID, "Void"),
        (STATUS_REFUNDED, "Refunded"),
    ]

    terminal = models.ForeignKey(PosTerminal, on_delete=models.PROTECT, related_name="sales")
    shift = models.ForeignKey(PosShift, on_delete=models.PROTECT, related_name="sales")
    refund_shift = models.ForeignKey(PosShift, on_delete=models.PROTECT, null=True, blank=True, related_name="refunded_sales")
    receipt_number = models.CharField(max_length=48)
    sale_date = models.DateField(default=timezone.localdate)
    customer = models.ForeignKey("parties.Party", on_delete=models.PROTECT, null=True, blank=True)
    sales_document = models.OneToOneField("sales.Document", on_delete=models.PROTECT, null=True, blank=True, related_name="pos_sale")
    refund_document = models.OneToOneField("sales.Document", on_delete=models.PROTECT, null=True, blank=True, related_name="pos_refund_sale")
    gl_entry = models.ForeignKey("accounting.Entry", on_delete=models.PROTECT, null=True, blank=True, related_name="pos_sales")
    reversal_entry = models.ForeignKey("accounting.Entry", on_delete=models.PROTECT, null=True, blank=True, related_name="pos_reversals")
    subtotal = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    discount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    tax = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    total = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    payment_status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PAID)
    idempotency_key = models.CharField(max_length=96, unique=True, null=True, blank=True)
    void_reason = models.CharField(max_length=500, blank=True)
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(default=timezone.now)
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="pos_sales")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["terminal", "sale_date", "receipt_number"], name="pos_sale_term_day_receipt"),
        ]
        indexes = [
            models.Index(fields=["shift", "payment_status"], name="pos_sale_shift_status"),
        ]


class PosSaleLine(models.Model):
    sale = models.ForeignKey(PosSale, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey("inventory.Item", on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal("0.0000"))
    discount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    line_total = models.DecimalField(max_digits=18, decimal_places=2)
    tax_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))


class PosPayment(models.Model):
    METHOD_CASH = "cash"
    METHOD_CARD = "card"
    METHOD_MANUAL = "manual"
    METHOD_CHOICES = [
        (METHOD_CASH, "Cash"),
        (METHOD_CARD, "Card"),
        (METHOD_MANUAL, "Manual"),
    ]

    sale = models.ForeignKey(PosSale, on_delete=models.CASCADE, related_name="payments")
    method = models.CharField(max_length=16, choices=METHOD_CHOICES)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    reference = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
