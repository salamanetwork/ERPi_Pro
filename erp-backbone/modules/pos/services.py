from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from accounting.models import Account, Entry
from accounting.services import ledger_lock, period_for, write_lines
from backbone.http import Problem
from inventory.models import Item, Location, Movement, Position, Valuation
from parties.models import Party
from sales.models import Customer, Document as SalesDocument
from .models import PosPayment, PosSale, PosSaleLine, PosShift, PosTerminal

TWOPLACES = Decimal("0.01")
FOURPLACES = Decimal("0.0001")


def _q2(val):
    return Decimal(str(val or "0")).quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def _q4(val):
    return Decimal(str(val or "0")).quantize(FOURPLACES, rounding=ROUND_HALF_UP)


def _get_or_create_walkin_customer(site):
    code = f"WALKIN-S{site.id}"[:24]
    customer = Customer.objects.select_related("party").filter(code=code).first()
    if customer is not None:
        return customer.party, customer
    party = Party.objects.create(
        site=site,
        party_type="customer",
        name_en=f"Walk-in Customer ({site.name})",
        name_ar=f"عميل نقدي عابر ({site.name})",
    )
    customer = Customer.objects.create(
        party=party,
        code=code,
        name=party.name_en,
        terms=0,
    )
    return party, customer


def _resolve_gl_accounts(terminal):
    ledger = ledger_lock()
    cash_acc = terminal.cash_account
    card_acc = terminal.card_account
    rev_acc = (
        terminal.revenue_account
        or Account.objects.filter(ledger=ledger, kind="revenue", active=True, detail=True).order_by("code").first()
        or Account.objects.filter(ledger=ledger, kind="revenue", active=True).order_by("code").first()
    )
    tax_acc = (
        terminal.tax_account
        or Account.objects.filter(ledger=ledger, kind="liability", active=True, detail=True, code__icontains="VAT").order_by("code").first()
        or Account.objects.filter(ledger=ledger, kind="liability", active=True, detail=True, code__icontains="TAX").order_by("code").first()
    )
    cogs_acc = (
        terminal.cogs_account
        or Account.objects.filter(ledger=ledger, kind="expense", active=True, detail=True, code__icontains="COGS").order_by("code").first()
        or Account.objects.filter(ledger=ledger, kind="expense", active=True, detail=True).order_by("code").first()
    )
    inv_acc = (
        terminal.inventory_account
        or Account.objects.filter(ledger=ledger, kind="asset", active=True, detail=True, code__icontains="INV").exclude(pk=cash_acc.pk).order_by("code").first()
    )
    over_short_acc = terminal.over_short_account or cogs_acc
    if not rev_acc:
        raise Problem("pos_revenue_account_missing", 409)
    return ledger, cash_acc, card_acc, rev_acc, tax_acc, cogs_acc, inv_acc, over_short_acc


@transaction.atomic
def open_shift(*, terminal: PosTerminal, cashier, opening_balance=Decimal("0.00")) -> PosShift:
    if not terminal.is_active:
        raise Problem("pos_terminal_inactive", 409)
    if PosShift.objects.select_for_update(of=("self",)).filter(terminal=terminal, status=PosShift.STATUS_OPEN).exists():
        raise Problem("pos_shift_already_open", 409)
    bal = _q2(opening_balance)
    if bal < Decimal("0.00"):
        raise Problem("pos_invalid_opening_balance", 400)
    return PosShift.objects.create(
        terminal=terminal,
        cashier=cashier,
        opening_balance=bal,
        expected_closing=bal,
        status=PosShift.STATUS_OPEN,
    )


def compute_shift_report(shift: PosShift) -> dict:
    from django.db.models import Q

    valid_sales = shift.sales.filter(payment_status__in=[PosSale.STATUS_PAID, PosSale.STATUS_PARTIAL])
    void_sales = shift.sales.filter(payment_status=PosSale.STATUS_VOID)
    collected_sales = shift.sales.exclude(payment_status=PosSale.STATUS_VOID)
    refunded_sales = PosSale.objects.filter(
        payment_status=PosSale.STATUS_REFUNDED
    ).filter(
        Q(refund_shift=shift) | Q(refund_shift__isnull=True, shift=shift)
    )

    gross_sales = _q2(valid_sales.aggregate(s=Sum("total"))["s"])
    total_tax = _q2(valid_sales.aggregate(s=Sum("tax"))["s"])
    total_discount = _q2(valid_sales.aggregate(s=Sum("discount"))["s"])
    subtotal = _q2(valid_sales.aggregate(s=Sum("subtotal"))["s"])

    payments_qs = PosPayment.objects.filter(sale__in=collected_sales)
    cash_sales = _q2(payments_qs.filter(method=PosPayment.METHOD_CASH).aggregate(s=Sum("amount"))["s"])
    card_sales = _q2(payments_qs.filter(method=PosPayment.METHOD_CARD).aggregate(s=Sum("amount"))["s"])
    manual_sales = _q2(payments_qs.filter(method=PosPayment.METHOD_MANUAL).aggregate(s=Sum("amount"))["s"])

    refund_payments_qs = PosPayment.objects.filter(sale__in=refunded_sales, method=PosPayment.METHOD_CASH)
    cash_refunds = _q2(refund_payments_qs.aggregate(s=Sum("amount"))["s"])

    expected_closing = _q2(shift.opening_balance + cash_sales - cash_refunds)

    top_items_raw = (
        PosSaleLine.objects.filter(sale__in=valid_sales)
        .values("item_id", "item__code", "item__name")
        .annotate(qty=Sum("quantity"), revenue=Sum("line_total"))
        .order_by("-revenue")[:10]
    )
    top_items = [
        {
            "item_id": row["item_id"],
            "code": row["item__code"],
            "name": row["item__name"],
            "quantity": str(row["qty"]),
            "revenue": str(_q2(row["revenue"])),
        }
        for row in top_items_raw
    ]

    return {
        "shift_id": shift.id,
        "terminal_id": shift.terminal_id,
        "terminal_code": shift.terminal.code,
        "terminal_name": shift.terminal.name,
        "cashier": getattr(shift.cashier, "username", str(shift.cashier_id)),
        "status": shift.status,
        "opened_at": shift.opened_at.isoformat(),
        "closed_at": shift.closed_at.isoformat() if shift.closed_at else None,
        "opening_balance": str(shift.opening_balance),
        "subtotal": str(subtotal),
        "discount": str(total_discount),
        "tax": str(total_tax),
        "total_sales": str(gross_sales),
        "sale_count": valid_sales.count(),
        "void_count": void_sales.count(),
        "refund_count": refunded_sales.count(),
        "payments": {
            "cash": str(cash_sales),
            "card": str(card_sales),
            "manual": str(manual_sales),
        },
        "cash_refunds": str(cash_refunds),
        "expected_closing": str(expected_closing),
        "closing_balance": str(shift.closing_balance) if shift.closing_balance is not None else None,
        "cash_variance": str(shift.cash_variance),
        "top_items": top_items,
    }


@transaction.atomic
def close_shift(*, shift: PosShift, closing_balance, actor: str, notes: str = "") -> PosShift:
    shift = PosShift.objects.select_for_update(of=("self",)).select_related("terminal", "terminal__cash_account", "terminal__over_short_account").get(pk=shift.pk)
    if shift.status != PosShift.STATUS_OPEN:
        raise Problem("pos_shift_not_open", 409)

    report = compute_shift_report(shift)
    expected = Decimal(report["expected_closing"])
    counted = _q2(closing_balance)
    if counted < Decimal("0.00"):
        raise Problem("pos_invalid_closing_balance", 400)
    variance = _q2(counted - expected)

    shift.closing_balance = counted
    shift.expected_closing = expected
    shift.cash_variance = variance
    shift.closed_at = timezone.now()
    shift.status = PosShift.STATUS_CLOSED
    if notes:
        shift.notes = notes[:500]
    shift.revision += 1
    shift.save(update_fields=["closing_balance", "expected_closing", "cash_variance", "closed_at", "status", "notes", "revision"])

    if variance != Decimal("0.00"):
        ledger, cash_acc, _, _, _, _, _, over_short_acc = _resolve_gl_accounts(shift.terminal)
        if not over_short_acc or over_short_acc.pk == cash_acc.pk:
            raise Problem("pos_overshort_account_missing", 409)
        if True:
            day = timezone.localdate()
            period = period_for(ledger, day)
            entry = Entry.objects.create(
                ledger=ledger,
                period=period,
                date=day,
                created_by=actor,
                posted_by=actor,
                posted_at=timezone.now(),
                description=f"POS Shift #{shift.id} Cash Over/Short Reconciliation",
                kind="pos_shift_close",
            )
            write_lines(entry, [
                (cash_acc, variance, shift.terminal.site_id, None),
                (over_short_acc, -variance, shift.terminal.site_id, None),
            ])

    return shift


@transaction.atomic
def checkout_sale(
    *,
    shift: PosShift,
    cashier,
    lines_data: list[dict],
    payments_data: list[dict],
    customer_party: Party | None = None,
    discount_amount=Decimal("0.00"),
    idempotency_key: str | None = None,
    sale_id_hint: str | None = None,
) -> PosSale:
    if not idempotency_key and sale_id_hint:
        idempotency_key = f"pos:sale:{sale_id_hint}"

    if idempotency_key:
        existing = PosSale.objects.filter(idempotency_key=idempotency_key).first()
        if existing is not None:
            return existing

    shift = PosShift.objects.select_for_update(of=("self",)).select_related(
        "terminal", "terminal__site", "terminal__cash_account", "terminal__card_account",
        "terminal__warehouse", "terminal__location"
    ).get(pk=shift.pk)
    if shift.status != PosShift.STATUS_OPEN:
        raise Problem("pos_shift_closed", 409)
    if not lines_data:
        raise Problem("pos_empty_cart", 400)
    if not payments_data:
        raise Problem("pos_payment_required", 400)

    terminal = shift.terminal
    site = terminal.site
    today = timezone.localdate()
    now = timezone.now()
    actor_name = getattr(cashier, "username", str(cashier.pk))

    location = terminal.location
    if location is None and terminal.warehouse_id:
        location = Location.objects.filter(warehouse_id=terminal.warehouse_id).order_by("id").first()

    # Pass 1: Validate items, stock, and compute net line totals before cart discount allocation
    prepared_lines = []
    subtotal = Decimal("0.00")
    line_discounts = Decimal("0.00")
    net_lines_sum = Decimal("0.00")

    for raw in lines_data:
        item_id = raw.get("item_id")
        barcode = (raw.get("barcode") or "").strip()
        if item_id:
            item = Item.objects.filter(pk=item_id, active=True, is_deleted=False).first()
        elif barcode:
            item = (
                Item.objects.filter(barcode=barcode, active=True, is_deleted=False).first()
                or Item.objects.filter(code=barcode, active=True, is_deleted=False).first()
            )
        else:
            item = None
        if item is None:
            raise Problem("pos_item_not_found", 404)

        qty = Decimal(str(raw.get("quantity", "1")))
        if qty <= Decimal("0"):
            raise Problem("pos_invalid_quantity", 400)

        unit_price = _q2(raw.get("unit_price") if raw.get("unit_price") is not None else item.default_price)
        l_disc = _q2(raw.get("discount", "0.00"))
        if l_disc < Decimal("0.00"):
            raise Problem("pos_invalid_discount", 400)
        gross_line = _q2(qty * unit_price)
        net_line = _q2(gross_line - l_disc)
        if net_line < Decimal("0.00"):
            raise Problem("pos_invalid_discount", 400)

        tax_rate = Decimal(str(raw.get("tax_rate") if raw.get("tax_rate") is not None else terminal.tax_rate))
        unit_cost = Decimal("0.0000")

        if location is not None:
            pos_row = Position.objects.select_for_update(of=("self",)).filter(site=site, item=item, location=location).first()
            available = pos_row.quantity if pos_row else Decimal("0")
            if available < qty:
                raise Problem("pos_insufficient_stock", 409)
            pos_row.quantity = available - qty
            pos_row.save(update_fields=["quantity"])

            val_row = Valuation.objects.select_for_update(of=("self",)).filter(site=site, item=item).first()
            if val_row and val_row.quantity > Decimal("0") and val_row.value > Decimal("0"):
                unit_cost = _q4(val_row.value / val_row.quantity)

            line_cost = _q2(qty * unit_cost)
            if val_row:
                new_val_qty = max(Decimal("0"), val_row.quantity - qty)
                if new_val_qty == Decimal("0"):
                    line_cost = _q2(val_row.value)
                    new_val_value = Decimal("0.00")
                else:
                    new_val_value = max(Decimal("0.00"), _q2(val_row.value - line_cost))
                val_row.quantity = new_val_qty
                val_row.value = new_val_value
                val_row.save(update_fields=["quantity", "value"])

            Movement.objects.create(
                site=site,
                item=item,
                location=location,
                kind="pos_sale",
                quantity=-qty,
                value=-line_cost,
                actor=actor_name,
                actor_id=cashier.pk,
                occurred_at=now,
                business_date=today,
                facts={"terminal": terminal.code, "shift_id": shift.id},
            )
        else:
            line_cost = Decimal("0.00")

        subtotal += gross_line
        line_discounts += l_disc
        net_lines_sum += net_line
        prepared_lines.append({
            "item": item,
            "quantity": qty,
            "unit_price": unit_price,
            "unit_cost": unit_cost,
            "line_cost": line_cost,
            "discount": l_disc,
            "net_line": net_line,
            "tax_rate": tax_rate,
        })

    cart_discount = _q2(discount_amount)
    if cart_discount < Decimal("0.00") or cart_discount > net_lines_sum:
        raise Problem("pos_invalid_discount", 400)

    # Pass 2: Proportionally allocate cart_discount across lines so VAT is computed on post-discount taxable base
    tax_total = Decimal("0.00")
    total_cogs = Decimal("0.00")
    remaining_cart_disc = cart_discount

    for idx, ln in enumerate(prepared_lines):
        if cart_discount > Decimal("0.00") and net_lines_sum > Decimal("0.00"):
            if idx == len(prepared_lines) - 1:
                alloc_disc = remaining_cart_disc
            else:
                alloc_disc = _q2(cart_discount * (ln["net_line"] / net_lines_sum))
                remaining_cart_disc = _q2(remaining_cart_disc - alloc_disc)
        else:
            alloc_disc = Decimal("0.00")

        taxable_line = _q2(ln["net_line"] - alloc_disc)
        tax_amt = _q2(taxable_line * ln["tax_rate"])
        ln["discount"] = _q2(ln["discount"] + alloc_disc)
        ln["line_total"] = taxable_line
        ln["tax_amount"] = tax_amt
        tax_total += tax_amt
        total_cogs += ln["line_cost"]

    total_discount = _q2(line_discounts + cart_discount)
    grand_total = _q2(subtotal - total_discount + tax_total)
    if grand_total < Decimal("0.00"):
        raise Problem("pos_invalid_total", 400)

    # Validate payments & handle cash change safely so GL stays strictly balanced
    paid_total = Decimal("0.00")
    cash_tendered = Decimal("0.00")
    card_paid = Decimal("0.00")
    manual_paid = Decimal("0.00")
    normalized_payments = []
    for p in payments_data:
        method = p.get("method", PosPayment.METHOD_CASH)
        if method not in (PosPayment.METHOD_CASH, PosPayment.METHOD_CARD, PosPayment.METHOD_MANUAL):
            raise Problem("pos_invalid_payment_method", 400)
        amt = _q2(p.get("amount", "0.00"))
        if amt <= Decimal("0.00"):
            raise Problem("pos_invalid_payment_amount", 400)
        paid_total += amt
        if method == PosPayment.METHOD_CASH:
            cash_tendered += amt
        elif method == PosPayment.METHOD_CARD:
            card_paid += amt
        else:
            manual_paid += amt
        normalized_payments.append({
            "method": method,
            "amount": amt,
            "reference": (p.get("reference") or "")[:120],
        })

    if paid_total < grand_total:
        raise Problem("pos_insufficient_payment", 409)

    overpayment = _q2(paid_total - grand_total)
    if overpayment > Decimal("0.00"):
        if cash_tendered < overpayment:
            raise Problem("pos_overpayment_exceeds_cash", 400)
        # Deduct change returned from the last cash payment so stored PosPayment equals net drawer cash
        rem_change = overpayment
        for p in reversed(normalized_payments):
            if p["method"] == PosPayment.METHOD_CASH and rem_change > Decimal("0.00"):
                deduct = min(p["amount"], rem_change)
                p["amount"] = _q2(p["amount"] - deduct)
                rem_change = _q2(rem_change - deduct)
        normalized_payments = [p for p in normalized_payments if p["amount"] > Decimal("0.00")]
    net_cash_paid = _q2(cash_tendered - overpayment)

    if customer_party is not None:
        sales_customer = Customer.objects.filter(party=customer_party).first()
        if sales_customer is None:
            sales_customer = Customer.objects.create(
                party=customer_party,
                code=f"POSP-{customer_party.pk}"[:24],
                name=customer_party.name_en or customer_party.name_ar or f"Customer {customer_party.pk}",
                terms=0,
            )
    else:
        customer_party, sales_customer = _get_or_create_walkin_customer(site)

    prefix = f"{terminal.code}-{today.strftime('%Y%m%d')}-"
    last_receipt = (
        PosSale.objects.filter(terminal=terminal, sale_date=today, receipt_number__startswith=prefix)
        .order_by("-receipt_number")
        .values_list("receipt_number", flat=True)
        .first()
    )
    last_seq = int(last_receipt.rsplit("-", 1)[-1]) if last_receipt and last_receipt.rsplit("-", 1)[-1].isdigit() else 0
    receipt_number = f"{prefix}{last_seq + 1:04d}"

    sales_doc = SalesDocument.objects.create(
        kind="receipt",
        site=site,
        customer=sales_customer,
        date=today,
        status="posted",
        lines=[
            {
                "item_id": ln["item"].id,
                "code": ln["item"].code,
                "name": ln["item"].name,
                "quantity": str(ln["quantity"]),
                "unit_price": str(ln["unit_price"]),
                "discount": str(ln["discount"]),
                "line_total": str(ln["line_total"]),
                "tax": str(ln["tax_amount"]),
            }
            for ln in prepared_lines
        ],
        snapshot={
            "doc_type": "POS_RECEIPT",
            "terminal_code": terminal.code,
            "shift_id": shift.id,
            "receipt_number": receipt_number,
        },
        amount=grand_total,
        actor=actor_name,
    )

    ledger, cash_acc, card_acc, rev_acc, tax_acc, cogs_acc, inv_acc, _ = _resolve_gl_accounts(terminal)
    period = period_for(ledger, today)
    gl_entry = Entry.objects.create(
        ledger=ledger,
        period=period,
        date=today,
        created_by=actor_name,
        posted_by=actor_name,
        posted_at=now,
        description=f"POS Sale {receipt_number}",
        kind="pos_sale",
    )
    net_revenue = _q2(grand_total - tax_total)
    gl_lines = []
    if net_cash_paid > Decimal("0.00"):
        gl_lines.append((cash_acc, net_cash_paid, site.id, None))
    if (card_paid + manual_paid) > Decimal("0.00"):
        if not card_acc:
            raise Problem("pos_card_account_missing", 409)
        gl_lines.append((card_acc, _q2(card_paid + manual_paid), site.id, None))
    if net_revenue > Decimal("0.00"):
        gl_lines.append((rev_acc, -net_revenue, site.id, None))
    if tax_total > Decimal("0.00"):
        if not tax_acc:
            raise Problem("pos_tax_account_missing", 409)
        gl_lines.append((tax_acc, -tax_total, site.id, None))
    if total_cogs > Decimal("0.00"):
        if not cogs_acc or not inv_acc or cogs_acc.pk == inv_acc.pk:
            raise Problem("pos_inventory_account_missing", 409)
        gl_lines.append((cogs_acc, total_cogs, site.id, None))
        gl_lines.append((inv_acc, -total_cogs, site.id, None))
    if gl_lines:
        write_lines(gl_entry, gl_lines)

    from django.db import IntegrityError

    try:
        sale = PosSale.objects.create(
            terminal=terminal,
            shift=shift,
            receipt_number=receipt_number,
            sale_date=today,
            customer=customer_party,
            sales_document=sales_doc,
            gl_entry=gl_entry,
            subtotal=_q2(subtotal),
            discount=total_discount,
            tax=tax_total,
            total=grand_total,
            payment_status=PosSale.STATUS_PAID,
            idempotency_key=idempotency_key,
            cashier=cashier,
        )
    except IntegrityError:
        if idempotency_key:
            existing = PosSale.objects.filter(idempotency_key=idempotency_key).first()
            if existing is not None:
                return existing
        raise
    if not sale.idempotency_key:
        sale.idempotency_key = f"pos:sale:{sale.pk}"
        sale.save(update_fields=["idempotency_key"])

    PosSaleLine.objects.bulk_create([
        PosSaleLine(
            sale=sale,
            item=ln["item"],
            quantity=ln["quantity"],
            unit_price=ln["unit_price"],
            unit_cost=ln["unit_cost"],
            discount=ln["discount"],
            line_total=ln["line_total"],
            tax_amount=ln["tax_amount"],
        )
        for ln in prepared_lines
    ])
    PosPayment.objects.bulk_create([
        PosPayment(
            sale=sale,
            method=p["method"],
            amount=p["amount"],
            reference=p["reference"],
        )
        for p in normalized_payments
    ])
    return sale


@transaction.atomic
def void_or_refund_sale(*, sale: PosSale, actor_user, reason: str) -> PosSale:
    if not (reason or "").strip():
        raise Problem("pos_refund_reason_required", 400)
    sale = PosSale.objects.select_for_update(of=("self",)).select_related(
        "shift", "terminal", "terminal__site", "terminal__cash_account", "terminal__card_account",
        "sales_document", "gl_entry"
    ).get(pk=sale.pk)
    if sale.payment_status in (PosSale.STATUS_VOID, PosSale.STATUS_REFUNDED):
        raise Problem("pos_sale_already_reversed", 409)

    actor_name = getattr(actor_user, "username", str(actor_user.pk))
    today = timezone.localdate()
    now = timezone.now()
    terminal = sale.terminal
    site = terminal.site

    location = terminal.location
    if location is None and terminal.warehouse_id:
        location = Location.objects.filter(warehouse_id=terminal.warehouse_id).order_by("id").first()
    if location is not None:
        for ln in sale.lines.select_related("item"):
            pos_row, _ = Position.objects.select_for_update(of=("self",)).get_or_create(
                site=site, item=ln.item, location=location, defaults={"quantity": Decimal("0")}
            )
            pos_row.quantity += ln.quantity
            pos_row.save(update_fields=["quantity"])

            line_cost = _q2(ln.quantity * ln.unit_cost)
            if line_cost > Decimal("0.00"):
                val_row, _ = Valuation.objects.select_for_update(of=("self",)).get_or_create(
                    site=site, item=ln.item, defaults={"quantity": Decimal("0"), "value": Decimal("0.00")}
                )
                val_row.quantity += ln.quantity
                val_row.value = _q2(val_row.value + line_cost)
                val_row.save(update_fields=["quantity", "value"])

            Movement.objects.create(
                site=site,
                item=ln.item,
                location=location,
                kind="pos_return",
                quantity=ln.quantity,
                value=line_cost,
                actor=actor_name,
                actor_id=actor_user.pk,
                occurred_at=now,
                business_date=today,
                facts={"receipt_number": sale.receipt_number, "reason": reason},
            )

    ledger, cash_acc, card_acc, rev_acc, tax_acc, _, _, _ = _resolve_gl_accounts(terminal)
    period = period_for(ledger, today)
    rev_entry = Entry.objects.create(
        ledger=ledger,
        period=period,
        date=today,
        created_by=actor_name,
        posted_by=actor_name,
        posted_at=now,
        description=f"POS Reversal {sale.receipt_number}: {reason[:120]}",
        kind="pos_reversal",
    )
    if sale.gl_entry_id:
        orig_lines = list(sale.gl_entry.lines.select_related("account"))
        if orig_lines:
            write_lines(rev_entry, [(ln.account, -ln.amount, ln.site_id, ln.cost_center_id) for ln in orig_lines])
    else:
        net_rev = _q2(sale.total - sale.tax)
        lines = [(rev_acc, net_rev, site.id, None), (cash_acc, -sale.total, site.id, None)]
        if sale.tax > Decimal("0.00") and tax_acc:
            lines.append((tax_acc, sale.tax, site.id, None))
        write_lines(rev_entry, lines)

    is_shift_open = sale.shift.status == PosShift.STATUS_OPEN
    if is_shift_open:
        sale.payment_status = PosSale.STATUS_VOID
        if sale.sales_document_id:
            sale.sales_document.status = "void"
            sale.sales_document.reason = reason[:1000]
            sale.sales_document.save(update_fields=["status", "reason"])
    else:
        sale.payment_status = PosSale.STATUS_REFUNDED
        if sale.sales_document_id:
            cn = SalesDocument.objects.create(
                kind="credit",
                site=site,
                customer=sale.sales_document.customer,
                source=sale.sales_document,
                date=today,
                status="posted",
                lines=sale.sales_document.lines,
                snapshot={"doc_type": "POS_CREDIT_NOTE", "receipt_number": sale.receipt_number},
                amount=sale.total,
                reason=reason[:1000],
                actor=actor_name,
            )
            sale.refund_document = cn

    if sale.payment_status == PosSale.STATUS_REFUNDED:
        active_shift = PosShift.objects.filter(terminal=terminal, status=PosShift.STATUS_OPEN).order_by("-id").first()
        sale.refund_shift = active_shift or sale.shift
    sale.void_reason = reason[:500]
    sale.reversal_entry = rev_entry
    sale.revision += 1
    sale.save(update_fields=["payment_status", "refund_shift", "void_reason", "reversal_entry", "refund_document", "revision"])
    return sale
