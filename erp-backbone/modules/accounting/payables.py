from collections import defaultdict
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import Q, Sum

from backbone.validation import field_path
from backbone.access import scopes, visible
from backbone.http import Problem, identifier
from backbone.models import Assignment
from backbone.services.documents import archive_rows
from inventory.models import Movement
from inventory.stock import prorate
from purchasing.money import decimal_value
from .models import Account, Accrual, InvoiceMatch, SupplierInvoice
from .services import period_for

ZERO = Decimal("0.00")


def posting_drafts(user):
    rows = SupplierInvoice.objects.filter(status="draft").exclude(created_by=user)
    rows = rows.exclude(pk__in=archive_rows("si").values("object_id"))
    return visible(visible(rows, user, "ap.view"), user, "ap.post")


def posting_reviewers(invoice):
    assigned = Assignment.objects.filter(Q(site_id=invoice.site_id) | Q(site__isnull=True))
    candidates = get_user_model().objects.filter(Q(pk__in=assigned.values("user_id")) | Q(is_superuser=True),
                                                 is_active=True).exclude(pk=invoice.created_by_id)
    return [
        candidate.get_username() for candidate in candidates.order_by("username")
        if all(scope & {None, invoice.site_id}
               for scope in (scopes(candidate, "ap.view"), scopes(candidate, "ap.post")))
    ]


def control(ledger, code, kind):
    account = Account.objects.filter(ledger=ledger, code=code, kind=kind,
                                     active=True, detail=True, control=True).first()
    if account is None:
        raise Problem("finance_account_unavailable", 409)
    return account


def posting_period(ledger, day):
    if not ledger.activated_at:
        raise Problem("ap_ledger_inactive", 409)
    if ledger.currency != "AED" or day < ledger.cutoff:
        raise Problem("finance_before_cutoff", 409)
    return period_for(ledger, day)


def receipt_state(move, accrual):
    matches = list(InvoiceMatch.objects.filter(accrual=accrual, invoice__reversal_entry__isnull=True).select_related("invoice__entry").order_by("pk")) if accrual else []
    reversals = list(move.reversals.select_related("posting__entry").order_by("pk"))
    used = defaultdict(lambda: [ZERO, ZERO])
    returned = ZERO
    for reversal in reversals:
        returned += Decimal(reversal.facts["purchase_quantity"])
        for part in reversal.facts.get("ap_return_parts", []):
            used[part["match_id"]][0] += Decimal(part["quantity"])
            used[part["match_id"]][1] += Decimal(part["amount"])
    billed_available = sum((match.quantity - used[match.pk][0] for match in matches), ZERO)
    available = Decimal(move.facts["purchase_quantity"]) - returned - billed_available
    return available, matches, reversals, used


def pending_reversals(reversals):
    return any(not hasattr(row, "posting") or row.posting.state == "pending" for row in reversals)


def match_preview(order, entries, posting_day=None, *, lock=False):
    ids = [item["movement_id"] for item in entries]
    query = Movement.objects.filter(pk__in=ids, kind="receipt", receipt__order=order,
                                    receipt__status="confirmed").select_related("receipt").order_by("pk")
    if lock:
        query = query.select_for_update(of=("self",))
    moves = {row.pk: row for row in query}
    balances = Accrual.objects.filter(receipt_movement_id__in=moves).select_related("entry", "account").order_by("pk")
    if lock:
        balances = balances.select_for_update(of=("self",))
    accruals = {row.receipt_movement_id: row for row in balances}
    result = []
    for item in entries:
        key = item["movement_id"]
        move, accrual = moves.get(key), accruals.get(key)
        output = {**item, "issues": [], "amount": "0.00"}
        result.append((output, accrual))
        risks = output["issues"]
        if not move:
            risks.append("ap_invalid_receipt")
            continue
        quantity = Decimal(item["quantity"])
        price = Decimal(item["unit_price"])
        expected = Decimal(move.facts["issued_unit_price"])
        available, _, reversals, _ = receipt_state(move, accrual)
        returned = sum((Decimal(row.facts["purchase_quantity"]) for row in reversals), ZERO)
        billed = Decimal(move.facts["purchase_quantity"]) - returned - available
        output.update(receipt=move.receipt.number, receipt_id=move.receipt_id, po_line_id=move.facts["po_line_id"],
                      item_id=move.item_id, unit_id=move.facts["purchase_unit_id"],
                      expected_price=str(expected), available=str(available), billed=str(billed), returned=str(returned))
        if quantity > available:
            risks.append("ap_quantity_conflict")
        if price != expected:
            risks.append("ap_price_mismatch")
        if accrual is None or pending_reversals(reversals):
            risks.append("ap_receipt_not_posted")
        if posting_day and (posting_day < move.business_date or
                            accrual and accrual.entry and posting_day < accrual.entry.date or
                            any(posting_day < row.business_date or (hasattr(row, "posting") and row.posting.entry_id and row.posting.entry.date > posting_day)
                                for row in reversals)):
            risks.append("ap_before_source")
        if accrual and not risks and quantity:
            amount = prorate(accrual.remaining, quantity, available)
            output["amount"] = str(amount)
    return result


def parse_lines(data):
    rows = data.get("lines")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
        raise Problem("invalid_lines")
    seen = set()
    lines = []
    for index, row in enumerate(rows):
        with field_path(f"lines.{index}"):
            if not isinstance(row, dict):
                raise Problem("invalid_lines")
            key = identifier(row.get("movement_id"), "movement")
            quantity = decimal_value(row.get("quantity"), 12, 3, "quantity")
            price = decimal_value(row.get("unit_price"), 12, 4, "unit_price")
            if key in seen:
                raise Problem("invalid_lines", field="movement_id", reason="duplicate")
            if quantity <= 0:
                raise Problem("invalid_lines", field="quantity", reason="min_value")
            seen.add(key)
            lines.append({"movement_id": key, "quantity": format(quantity, ".3f"), "unit_price": format(price, ".4f")})
    return lines


def exposure(invoice, day=None):
    rows = invoice.allocations.all()
    if day:
        rows = rows.filter(settlement__entry__date__lte=day)
    totals = dict(rows.values("settlement__kind").annotate(value=Sum("amount")).values_list("settlement__kind", "value"))
    paid, credited = totals.get("payment", ZERO), totals.get("credit", ZERO)
    return paid, credited, invoice.total - paid - credited


def return_split(move, quantity, legacy_cost, kind, day):
    # Called after the PO, site and ledger are locked. Use the received purchase unit.
    accrual = Accrual.objects.select_for_update().filter(receipt_movement=move).first()
    if not accrual:
        return legacy_cost, []
    available, matches, reversals, used = receipt_state(move, accrual)
    if not matches:
        return legacy_cost, []
    if reversals and pending_reversals(reversals):
        raise Problem("ap_receipt_not_posted", 409)
    unbilled = min(available, quantity)
    remaining = quantity - unbilled
    if remaining and kind != "return":
        raise Problem("ap_billed_correction", 409)
    cost = prorate(accrual.remaining, unbilled, available) if unbilled else ZERO
    parts = []
    for match in matches:
        if not remaining:
            break
        prior_qty, prior_amount = used[match.pk]
        left = match.quantity - prior_qty
        take = min(left, remaining)
        if not take:
            continue
        if day < match.invoice.entry.date:
            raise Problem("ap_before_invoice", 409)
        amount = prorate(match.amount - prior_amount, take, left)
        parts.append({"match_id": match.pk, "quantity": str(take), "amount": str(amount)})
        remaining -= take
    if remaining:
        raise Problem("ap_quantity_conflict", 409)
    return cost, parts
