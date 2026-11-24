from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from accounting.models import Account, Entry, Period
from accounting.services import ledger_lock, period_for, posted_entry, write_lines
from backbone.http import Problem
from inventory.models import Item, Location, Movement, Position, Warehouse
from parties.models import Party
from sales.models import Customer, Document as SalesDocument
from .models import PosPayment, PosSale, PosSaleLine, PosShift, PosTerminal

TWOPLACES = Decimal("0.01")


def _q2(val):
    return Decimal(str(val or "0")).quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def _get_or_create_walkin_customer(site):
    stamp = timezone.now().strftime("%Y%m%d%H%M%S%f")
    code = f"WALKIN-{stamp[:18]}"
    party = Party.objects.create(
        site=site,
        party_type="customer",
        name_en=f"Walk-in Customer ({stamp[:14]})",
        name_ar=f"عميل نقدي عابر ({stamp[:14]})",
    )
    customer = Customer.objects.create(
        party=party,
        code=code[:24],
        name=party.name_en,
        terms=0,
    )
    return party, customer


def _resolve_gl_accounts(terminal):
    ledger = ledger_lock()
    cash_acc = terminal.cash_account
    card_acc = terminal.card_account or cash_acc
    rev_acc = (
        Account.objects.filter(ledger=ledger, kind="revenue", active=True, detail=True).order_by("code").first()
        or Account.objects.filter(ledger=ledger, kind="revenue", active=True).order_by("code").first()
    )
    tax_acc = (
        Account.objects.filter(ledger=ledger, kind="liability", active=True, detail=True).order_by("code").first()
        or Account.objects.filter(ledger=ledger, kind="liability", active=True).order_by("code").first()
        or rev_acc
    )
    cogs_acc = (
        Account.objects.filter(ledger=ledger, kind="expense", active=True, detail=True).order_by("code").first()
        or Account.objects.filter(ledger=ledger, kind="expense", active=True).order_by("code").first()
    )
    inv_acc = (
        Account.objects.filter(ledger=ledger, kind="asset", active=True, detail=True).exclude(pk=cash_acc.pk).order_by("code").first()
        or cash_acc
    )
    over_short_acc = terminal.over_short_account or cogs_acc or rev_acc
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
    valid_sales = shift.sales.filter(payment_status__in=[PosSale.STATUS_PAID, PosSale.STATUS_PARTIAL])
    void_sales = shift.sales.filter(payment_status=PosSale.STATUS_VOID)
    refunded_sales = shift.sales.filter(payment_status=PosSale.STATUS_REFUNDED)

    gross_sales = _q2(valid_sales.aggregate(s=Sum("total"))["s"])
    total_tax = _q2(valid_sales.aggregate(s=Sum("tax"))["s"])
    total_discount = _q2(valid_sales.aggregate(s=Sum("discount"))["s"])
    subtotal = _q2(valid_sales.aggregate(s=Sum("subtotal"))["s"])

    payments_qs = PosPayment.objects.filter(sale__in=valid_sales)
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
        if over_short_acc and over_short_acc.pk != cash_acc.pk:
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
            # variance > 0 => cash over (Dr Cash, Cr Over/Short)
            # variance < 0 => cash short (Dr Over/Short, Cr Cash)
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

    # Build lines and validate stock with SELECT FOR UPDATE
    prepared_lines = []
    subtotal = Decimal("0.00")
    line_discounts = Decimal("0.00")
    tax_total = Decimal("0.00")
    total_cogs = Decimal("0.00")

    location = terminal.location
    if location is None and terminal.warehouse_id:
        location = Location.objects.filter(warehouse_id=terminal.warehouse_id).order_by("id").first()

    for raw in lines_data:
        item_id = raw.get("item_id")
        barcode = (raw.get("barcode") or "").strip()
        if item_id:
            item = Item.objects.filter(pk=item_id, active=True).first()
        elif barcode:
            item = Item.objects.filter(barcode=barcode, active=True).first() or Item.objects.filter(code=barcode, active=True).first()
        else:
            item = None
        if item is None:
            raise Problem("pos_item_not_found", 404)

        qty = Decimal(str(raw.get("quantity", "1")))
        if qty <= Decimal("0"):
            raise Problem("pos_invalid_quantity", 400)

        unit_price = _q2(raw.get("unit_price") if raw.get("unit_price") is not None else item.default_price)
        l_disc = _q2(raw.get("discount", "0.00"))
        gross_line = _q2(qty * unit_price)
        net_line = _q2(gross_line - l_disc)
        if net_line < Decimal("0.00"):
            raise Problem("pos_invalid_discount", 400)

        tax_rate = Decimal(str(raw.get("tax_rate") if raw.get("tax_rate") is not None else terminal.tax_rate))
        tax_amt = _q2(net_line * tax_rate)

        # Lock inventory movements/position to prevent negative stock
        if location is not None:
            list(Movement.objects.select_for_update(of=("self",)).filter(site=site, item=item, location=location))
            pos_row = Position.objects.select_for_update(of=("self",)).filter(site=site, item=item, location=location).first()
            available = pos_row.quantity if pos_row else Decimal("0")
            if available < qty:
                raise Problem("pos_insufficient_stock", 409)
            pos_row.quantity = available - qty
            pos_row.save(update_fields=["quantity"])

            unit_cost = _q2(raw.get("unit_cost", "0.00"))
            line_cost = _q2(qty * unit_cost)
            total_cogs += line_cost
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

        subtotal += gross_line
        line_discounts += l_disc
        tax_total += tax_amt
        prepared_lines.append({
            "item": item,
            "quantity": qty,
            "unit_price": unit_price,
            "discount": l_disc,
            "line_total": net_line,
            "tax_amount": tax_amt,
        })

    cart_discount = _q2(discount_amount)
    total_discount = _q2(line_discounts + cart_discount)
    grand_total = _q2(subtotal - total_discount + tax_total)
    if grand_total < Decimal("0.00"):
        raise Problem("pos_invalid_total", 400)

    # Validate payments
    paid_total = Decimal("0.00")
    cash_paid = Decimal("0.00")
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
            cash_paid += amt
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

    # Resolve customer Party & sales.Customer
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

    seq = PosSale.objects.filter(terminal=terminal, sale_date=today).count() + 1
    receipt_number = f"{terminal.code}-{today.strftime('%Y%m%d')}-{seq:04d}"

    # Create sales.Document (POS_RECEIPT stored as kind='receipt' with pos snapshot)
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

    # Post GL Entry
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
    if cash_paid > Decimal("0.00"):
        gl_lines.append((cash_acc, cash_paid, site.id, None))
    if (card_paid + manual_paid) > Decimal("0.00"):
        gl_lines.append((card_acc, _q2(card_paid + manual_paid), site.id, None))
    if net_revenue > Decimal("0.00"):
        gl_lines.append((rev_acc, -net_revenue, site.id, None))
    if tax_total > Decimal("0.00") and tax_acc:
        gl_lines.append((tax_acc, -tax_total, site.id, None))
    if total_cogs > Decimal("0.00") and cogs_acc and inv_acc and cogs_acc.pk != inv_acc.pk:
        gl_lines.append((cogs_acc, total_cogs, site.id, None))
        gl_lines.append((inv_acc, -total_cogs, site.id, None))
    write_lines(gl_entry, gl_lines)

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
    if not sale.idempotency_key:
        sale.idempotency_key = f"pos:sale:{sale.pk}"
        sale.save(update_fields=["idempotency_key"])

    PosSaleLine.objects.bulk_create([
        PosSaleLine(
            sale=sale,
            item=ln["item"],
            quantity=ln["quantity"],
            unit_price=ln["unit_price"],
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

    # Restore stock if terminal tracks location
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
            Movement.objects.create(
                site=site,
                item=ln.item,
                location=location,
                kind="pos_return",
                quantity=ln.quantity,
                value=Decimal("0.00"),
                actor=actor_name,
                actor_id=actor_user.pk,
                occurred_at=now,
                business_date=today,
                facts={"receipt_number": sale.receipt_number, "reason": reason},
            )

    # Reverse GL lines
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
        write_lines(rev_entry, [(ln.account, -ln.amount, ln.site_id, ln.cost_center_id) for ln in orig_lines])
    else:
        net_rev = _q2(sale.total - sale.tax)
        lines = [(rev_acc, net_rev, site.id, None), (cash_acc, -sale.total, site.id, None)]
        if sale.tax > Decimal("0.00") and tax_acc:
            lines.append((tax_acc, sale.tax, site.id, None))
        write_lines(rev_entry, lines)

    is_shift_open = sale.shift.status == PosShift.STATUS_OPEN
    if is_shift_open:
        # Void before shift close
        sale.payment_status = PosSale.STATUS_VOID
        if sale.sales_document_id:
            sale.sales_document.status = "void"
            sale.sales_document.reason = reason[:1000]
            sale.sales_document.save(update_fields=["status", "reason"])
    else:
        # Refund after shift close -> create linked Credit Note in sales.Document
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

    sale.void_reason = reason[:500]
    sale.reversal_entry = rev_entry
    sale.revision += 1
    sale.save(update_fields=["payment_status", "void_reason", "reversal_entry", "refund_document", "revision"])
    return sale
