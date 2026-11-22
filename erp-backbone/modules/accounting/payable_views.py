from backbone.extensions import invoke_extension
from collections import defaultdict
from decimal import Decimal
from unicodedata import normalize

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.views.decorators.cache import never_cache

from backbone.access import audit, visible
from backbone.http import Problem, body, endpoint, identifier, once, require, string
from backbone.services.documents import archive_rows, reject_archived
from inventory.models import Movement
from purchasing.models import PurchaseOrder
from purchasing.money import check_total, decimal_value
from .models import Account, Accrual, InvoiceMatch, SettlementAllocation, SupplierInvoice, SupplierSettlement
from .payables import ZERO, control, exposure, match_preview, parse_lines, posting_drafts, posting_period, posting_reviewers
from .services import ledger_lock, posted_entry
from .views import day_field



def require_ap(user, action="view", site=...):
    require(user, "ap.view", site)
    if action != "view":
        require(user, "ap." + action, site)


def source(user, key):
    row = visible(PurchaseOrder.objects.filter(status="issued", currency="AED"), user, "ap.view").filter(pk=key).first()
    if row is None:
        raise Problem("ap_order_unavailable", 404)
    return row


def find(user, pk, lock=False):
    rows = visible(SupplierInvoice.objects.all(), user, "ap.view")
    if lock:
        rows = rows.select_for_update()
    row = rows.select_related("supplier", "order", "entry").filter(pk=pk) if not lock else rows.filter(pk=pk)
    row = row.first()
    if row is None:
        raise Problem("not_found", 404)
    return row


def serialize(row, detail=False):
    result = {"id": row.pk, "number": row.document_number, "supplier_number": row.supplier_number,
              "supplier_id": row.supplier_id, "supplier": row.snapshot.get("supplier", row.supplier.name),
              "site_id": row.site_id, "order_id": row.order_id, "order": row.order.number,
              "invoice_date": row.invoice_date.isoformat(), "due_date": row.due_date.isoformat(),
              "status": row.status, "revision": row.revision, "total": str(row.total),
              "declared_total": str(row.declared_total),
              "posting_date": row.entry.date.isoformat() if row.entry_id else None, "entry_id": row.entry_id,
              "reversal_entry_id": row.reversal_entry_id}
    if detail:
        paid, credited, balance = exposure(row)
        if row.status == "reversed":
            balance = ZERO
        result.update(lines=row.draft_lines, match=row.snapshot.get("match", []),
                      paid=str(paid), credited=str(credited), balance=str(balance),
                      settlements=[{"kind": alloc.settlement.kind, "reference": alloc.settlement.reference,
                          "entry_id": alloc.settlement.entry_id, "amount": str(alloc.amount),
                          "date": alloc.settlement.entry.date.isoformat(), "reason": alloc.settlement.reason,
                          "return_id": alloc.return_movement_id}
                          for alloc in row.allocations.select_related("settlement__entry").order_by("pk")])
    return result


def invoice_fields(data):
    number = string(data, "supplier_number", 100)
    number_key = normalize("NFKC", number).casefold()
    if len(number_key) > 300 or any(ord(char) < 32 for char in number):
        raise Problem("invalid_supplier_number")
    day, due = day_field(data, "invoice_date"), day_field(data, "due_date")
    if due < day:
        raise Problem("ap_due_before_invoice")
    return {"supplier_number": number, "supplier_number_key": number_key, "invoice_date": day,
            "due_date": due, "draft_lines": parse_lines(data),
            "declared_total": decimal_value(data.get("declared_total"), 15, 2, "declared_total")}


def validated_fields(order, data):
    fields = invoice_fields(data)
    if any("ap_invalid_receipt" in result["issues"] for result, _ in match_preview(order, fields["draft_lines"])):
        raise Problem("ap_invalid_receipt", 409)
    return fields


def save_number(callback):
    try:
        with transaction.atomic():
            return callback()
    except IntegrityError as failure:
        if getattr(getattr(failure.__cause__, "diag", None), "constraint_name", None) == "ap_supplier_invoice_number":
            raise Problem("ap_duplicate_invoice", 409) from None
        raise


@never_cache
@endpoint("GET", "POST", module="supplier_invoices")
def invoices(request):
    user = request.user
    require_ap(user)
    if request.method == "GET":
        rows = visible(SupplierInvoice.objects.select_related("supplier", "order", "entry"), user, "ap.view")
        if "page_size" in request.GET:
            from backbone.services.pagination import operational_page
            return operational_page(request,rows.exclude(pk__in=archive_rows("si").values("object_id")).order_by("-id"),serialize,['supplier_number', 'supplier__name', 'status'])
        return {"items": [serialize(row) for row in rows.exclude(pk__in=archive_rows("si").values("object_id")).order_by("-id")]}
    data = body(request)
    order = source(user, identifier(data.get("order_id"), "order"))
    require_ap(user, "create", order.site_id)

    def create():
        from backbone.site_admin import require_active_site
        from purchasing.orders import active_supplier

        require_active_site(order.site_id, lock=True)
        active_supplier(order.supplier_id)
        fields = validated_fields(order, data)
        row = save_number(lambda: SupplierInvoice.objects.create(order=order, supplier=order.supplier,
                          site_id=order.site_id, created_by=user, **fields))
        audit(user, "ap.invoice.create", f"{row.document_number} / {order.number}")
        return serialize(row, True)

    return once(request, "ap.invoice.create", data, create)


@never_cache
@endpoint("GET", "PATCH", module="supplier_invoices")
def invoice(request, pk):
    user = request.user
    if request.method == "GET":
        row = find(user, pk)
        reject_archived("si", row)
        result = {**serialize(row, True), "can_post": posting_drafts(user).filter(pk=row.pk).exists()}
        if user.is_superuser and row.status == "draft":
            result["posting_routing"] = {"site_id": row.site_id, "created_by": row.created_by.get_username(),
                                         "eligible_reviewers": posting_reviewers(row)}
        return result
    data = body(request)
    with transaction.atomic():
        row = find(user, pk, lock=True)
        require_ap(user, "create", row.site_id)
        draft(row, data)
        for key, value in validated_fields(row.order, data).items():
            setattr(row, key, value)
        row.revision += 1
        save_number(row.save)
        audit(user, "ap.invoice.edit", f"{row.document_number} / rev {row.revision}")
        return serialize(row, True)


def draft(row, data):
    reject_archived("si", row)
    if row.status != "draft" or identifier(data.get("revision"), "revision") != row.revision:
        raise Problem("revision_conflict", 409)


@never_cache
@endpoint("GET", module="supplier_invoices")
def orders(request):
    user = request.user
    require_ap(user)
    rows = visible(PurchaseOrder.objects.filter(status="issued", currency="AED",
                   receipt__status="confirmed").distinct(), user, "ap.view").order_by("-id")
    return {"items": [{"id": row.pk, "number": row.number,
                       "supplier": row.snapshot["supplier"]["name"], "site_id": row.site_id} for row in rows]}


@never_cache
@endpoint("GET", module="supplier_invoices")
def receipts(request, pk):
    order = source(request.user, pk)
    moves = Movement.objects.filter(receipt__order=order, receipt__status="confirmed", kind="receipt").order_by("pk")
    lines = [{"movement_id": move.pk, "quantity": "0.000",
              "unit_price": move.facts["issued_unit_price"]} for move in moves]
    return {"items": [output for output, _ in match_preview(order, lines)]}


@never_cache
@endpoint("POST", module="supplier_invoices")
def match(request, pk):
    row = find(request.user, pk)
    data = body(request)
    draft(row, data)
    day = day_field(data, "posting_date")
    matches = [output for output, _ in match_preview(row.order, row.draft_lines, day)]
    total = sum((Decimal(item["amount"]) for item in matches), ZERO)
    issues = []
    if total != row.declared_total:
        issues.append("ap_total_mismatch")
    if day < row.invoice_date:
        issues.append("ap_before_invoice")
    return {"items": matches, "total": str(total), "declared_total": str(row.declared_total),
            "issues": issues, "matched": not issues and all(not item["issues"] for item in matches)}


@never_cache
@endpoint("POST", module="supplier_invoices")
def post_invoice(request, pk):
    user = request.user
    row = find(user, pk)
    require_ap(user, "post", row.site_id)
    data = body(request)
    day = day_field(data, "posting_date")

    def commit():
        current = find(user, pk, lock=True)
        draft(current, data)
        if current.created_by_id == user.pk:
            raise Problem("ap_separate_poster", 403)
        if day < current.invoice_date:
            raise Problem("ap_before_invoice", 409)
        # Stock takes PO -> site -> ledger. Do not acquire a PO/site lock after the ledger.
        ledger = ledger_lock()
        period = posting_period(ledger, day)
        checked = match_preview(current.order, current.draft_lines, day, lock=True)
        for output, _ in checked:
            if output["issues"]:
                raise Problem(output["issues"][0], 409)
        invoke_extension("project_purchase_new",current.order_id,
                         [accrual.receipt_movement.facts.get("po_line_id") for _,accrual in checked])
        total = sum((Decimal(output["amount"]) for output, _ in checked), ZERO)
        check_total(total)
        if total != current.declared_total:
            raise Problem("ap_total_mismatch", 409)
        ap = control(ledger, "AP-CONTROL", "liability")
        values = [(accrual.account, Decimal(output["amount"]), current.site_id, None) for output, accrual in checked]
        values.append((ap, -total, current.site_id, None))
        if not total:
            raise Problem("ap_zero_invoice", 409)
        entry = posted_entry(ledger, period, day, user.get_username(), f"Supplier invoice {current.document_number}",
                             "ap_invoice", values, source_date=current.invoice_date)
        for output, accrual in checked:
            amount = Decimal(output["amount"])
            InvoiceMatch.objects.create(invoice=current, accrual=accrual, quantity=output["quantity"], amount=amount)
            accrual.remaining -= amount
            accrual.save(update_fields=["remaining"])
        current.status, current.total, current.entry = "posted", total, entry
        current.revision += 1
        current.snapshot = {"supplier": current.order.snapshot["supplier"]["name"], "currency": "AED",
                            "match": [output for output, _ in checked]}
        current.save(update_fields=["status", "total", "entry", "revision", "snapshot"])
        audit(user, "ap.invoice.post", f"{current.document_number} / {current.order.number} / {entry.number}")
        return serialize(current, True)

    return once(request, "ap.invoice.post", {"id": pk, **data}, commit)


def settlement_data(row):
    return {"id": row.pk, "kind": row.kind, "reference": row.reference, "total": str(row.total),
            "entry_id": row.entry_id, "date": row.entry.date.isoformat()}


def post_settlement(user, ledger, period, day, kind, invoice, data, allocations, debit, credit):
    reference = string(data, "reference", 100)
    reason = string(data, "reason", 1000, required=kind == "credit")
    if SupplierSettlement.objects.filter(supplier=invoice.supplier, kind=kind, reference=reference).exists():
        raise Problem("ap_duplicate_settlement", 409)
    total = sum((row["amount"] for row in allocations), ZERO)
    check_total(total)
    if total <= 0:
        raise Problem("ap_zero_settlement", 409)
    entry = posted_entry(ledger, period, day, user.get_username(), f"Supplier {kind} {reference}",
                         "ap_" + kind, [(debit, total, invoice.site_id, None), (credit, -total, invoice.site_id, None)])
    settlement = SupplierSettlement.objects.create(supplier=invoice.supplier, site_id=invoice.site_id,
        kind=kind, reference=reference, reason=reason, total=total, entry=entry)
    SettlementAllocation.objects.bulk_create([SettlementAllocation(settlement=settlement, **row) for row in allocations])
    audit(user, "ap." + kind, f"{reference} / {invoice.supplier.code} / {entry.number}")
    return settlement_data(settlement)


@never_cache
@endpoint("POST", module="supplier_invoices")
def payments(request):
    user = request.user
    data = body(request)
    day = day_field(data, "posting_date")
    lines = data.get("allocations")
    if not isinstance(lines, list) or not 1 <= len(lines) <= 100:
        raise Problem("invalid_lines")
    amounts = {}
    for line in lines:
        if not isinstance(line, dict):
            raise Problem("invalid_lines")
        key = identifier(line.get("invoice_id"), "invoice")
        amount = decimal_value(line.get("amount"), 15, 2, "amount")
        if key in amounts or amount <= 0:
            raise Problem("invalid_lines")
        amounts[key] = amount
    initial = find(user, next(iter(amounts)))
    require_ap(user, "pay", initial.site_id)

    def commit():
        invoices = [find(user, pk, lock=True) for pk in sorted(amounts)]
        ledger = ledger_lock()
        period = posting_period(ledger, day)
        allocations = []
        for row in invoices:
            require_ap(user, "pay", row.site_id)
            if row.supplier_id != initial.supplier_id or row.site_id != initial.site_id:
                raise Problem("ap_payment_scope", 409)
            if row.status != "posted" or not row.entry_id or day < row.entry.date:
                raise Problem("ap_before_invoice", 409)
            if amounts[row.pk] > exposure(row)[2]:
                raise Problem("ap_balance_conflict", 409)
            allocations.append({"invoice": row, "amount": amounts[row.pk]})
        bank = Account.objects.filter(ledger=ledger, code="1100", kind="asset", detail=True, active=True, control=False).first()
        if not bank:
            raise Problem("finance_account_unavailable", 409)
        return post_settlement(user, ledger, period, day, "payment", invoices[0], data, allocations,
                               control(ledger, "AP-CONTROL", "liability"), bank)

    return once(request, "ap.payment", data, commit)


def open_credits(row):
    if row.status != "posted":
        return
    matches = {match.pk: match for match in row.matches.all()}
    credited = set(row.allocations.filter(settlement__kind="credit").values_list("return_movement_id", "match_id"))
    movements = Movement.objects.filter(original__accrual__in=[match.accrual_id for match in matches.values()],
                                       kind="return").select_related("receipt", "posting__entry").order_by("pk")
    for move in movements:
        for part in move.facts.get("ap_return_parts", []):
            match = matches.get(part["match_id"])
            if match and (move.pk, match.pk) not in credited and Decimal(part["amount"]) > 0:
                yield move, match, part


@never_cache
@endpoint("GET", "POST", module="supplier_invoices")
def credits(request, pk):
    user = request.user
    row = find(user, pk)
    if request.method == "GET":
        return {"items": [{"movement_id": move.pk, "receipt": move.receipt.number, "match_id": match.pk,
                            "amount": part["amount"], "quantity": part["quantity"],
                            "ready": hasattr(move, "posting") and bool(move.posting.entry_id)}
                           for move, match, part in open_credits(row)]}
    require_ap(user, "credit", row.site_id)
    data = body(request)
    day = day_field(data, "posting_date")
    move_id = identifier(data.get("movement_id"), "movement")

    def commit():
        current = find(user, pk, lock=True)
        ledger = ledger_lock()
        period = posting_period(ledger, day)
        allocations = []
        for move, match, part in open_credits(current):
            if move.pk != move_id:
                continue
            if not hasattr(move, "posting") or not move.posting.entry_id:
                raise Problem("ap_return_not_posted", 409)
            if day < max(current.entry.date, move.posting.entry.date):
                raise Problem("ap_before_source", 409)
            allocations.append({"invoice": current, "return_movement": move, "match": match,
                                "amount": Decimal(part["amount"])})
        if not allocations:
            raise Problem("ap_credit_unavailable", 409)
        declared = decimal_value(data.get("amount"), 15, 2, "amount")
        if declared != sum((item["amount"] for item in allocations), ZERO):
            raise Problem("ap_total_mismatch", 409)
        return post_settlement(user, ledger, period, day, "credit", current, data, allocations,
                               control(ledger, "AP-CONTROL", "liability"), control(ledger, "AP-RETURNS", "asset"))

    return once(request, "ap.credit", {"id": pk, **data}, commit)


@never_cache
@endpoint("POST", module="supplier_invoices")
def reverse_invoice(request, pk):
    user = request.user
    row = find(user, pk)
    require_ap(user, "post", row.site_id)
    data = body(request)
    day = day_field(data, "posting_date")
    reason = string(data, "reason", 1000)

    def commit():
        row = find(user, pk, lock=True)
        if row.revision != identifier(data.get("revision"), "revision") or row.status != "posted":
            raise Problem("revision_conflict", 409)
        if row.created_by_id == user.pk:
            raise Problem("ap_separate_poster", 403)
        ledger = ledger_lock()
        period = posting_period(ledger, day)
        if day < row.entry.date:
            raise Problem("ap_before_invoice", 409)
        if row.allocations.exists():
            raise Problem("ap_reverse_settled", 409)
        matches = list(row.matches.select_related("accrual").order_by("accrual_id"))
        ids = [match.pk for match in matches]
        source_ids = [match.accrual.receipt_movement_id for match in matches]
        for move in Movement.objects.filter(original_id__in=source_ids):
            if any(part["match_id"] in ids for part in move.facts.get("ap_return_parts", [])):
                raise Problem("ap_reverse_returned", 409)
        balances = Accrual.objects.select_for_update().in_bulk([match.accrual_id for match in matches])
        entry = posted_entry(ledger, period, day, user.get_username(), f"Reverse {row.document_number}: {reason}",
                             "ap_reverse", [(line.account, -line.amount, line.site_id, None)
                             for line in row.entry.lines.select_related("account")], reversal_of=row.entry)
        for match in matches:
            accrual = balances[match.accrual_id]
            accrual.remaining += match.amount
            accrual.save(update_fields=["remaining"])
        row.status, row.reversal_entry, row.revision = "reversed", entry, row.revision + 1
        row.save(update_fields=["status", "reversal_entry", "revision"])
        audit(user, "ap.invoice.reverse", f"{row.document_number} / {entry.number} / {reason}")
        return serialize(row, True)

    return once(request, "ap.invoice.reverse", {"id": pk, **data}, commit)


@never_cache
@endpoint("GET", module="supplier_invoices")
def aging(request):
    user = request.user
    require_ap(user)
    day = day_field(request.GET, "as_of")
    rows = visible(SupplierInvoice.objects.filter(entry__date__lte=day).filter(
                       Q(reversal_entry__isnull=True) | Q(reversal_entry__date__gt=day)).select_related("supplier", "order", "entry"),
                   user, "ap.view")
    for key in ("site", "supplier"):
        value = request.GET.get(key)
        if value:
            if not value.isascii() or not value.isdecimal() or len(value) > 18:
                raise Problem("invalid_filter")
            rows = rows.filter(**{key + "_id": int(value)})
    totals = defaultdict(lambda: ZERO)
    items = []
    for row in rows.order_by("due_date", "pk"):
        paid, credited, balance = exposure(row, day)
        if not balance:
            continue
        days = (day - row.due_date).days
        bucket = ("credit" if balance < 0 else "current" if days <= 0 else "1-30" if days <= 30
                  else "31-60" if days <= 60 else "61-90" if days <= 90 else "90+")
        totals[bucket] += balance
        items.append({**serialize(row), "paid": str(paid), "credited": str(credited),
                      "balance": str(balance), "bucket": bucket})
    return {"as_of": day.isoformat(), "items": items, "total": str(sum(totals.values(), ZERO)),
            "buckets": {key: str(value) for key, value in totals.items()}}
