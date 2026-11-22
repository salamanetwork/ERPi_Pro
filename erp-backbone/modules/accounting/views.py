import re
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import F, Q, Sum
from django.utils import timezone

from backbone.validation import field_path
from backbone.access import audit
from backbone.http import Problem, body, endpoint, identifier, once, require, site, string
from purchasing.money import decimal_value
from .chart import KINDS
from .centers import center_for_site
from .models import Account, Accrual, Entry, InvoiceMatch, Ledger, Line, Period, Posting, Reference, Rule, SettlementAllocation, SupplierInvoice
from .services import activate, ledger_lock, month_end, period_for, preview, process, write_lines

MODULE = "accounting"


def require_finance(user, permission="view"):
    require(user, "accounting.view", None)
    if permission != "view":
        require(user, f"accounting.{permission}", None)


def day_field(data, field, *, error=None):
    value = data.get(field)
    try:
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError
        return date.fromisoformat(value)
    except ValueError:
        raise Problem(error or f"invalid_{field}", field=field,
                      reason="required" if value in (None, "") else "invalid_date") from None


def find(model, ledger, key, **filters):
    row = model.objects.filter(ledger=ledger, pk=identifier(key, "id"), **filters).first()
    if row is None:
        raise Problem("not_found", 404)
    return row


@endpoint("GET", module=MODULE)
def setup(request):
    require_finance(request.user)
    ledger = Ledger.objects.get(pk=1)
    return {"currency": ledger.currency, "cutoff": ledger.cutoff, "activated_at": ledger.activated_at,
            "activated_by": ledger.activated_by,
            "accounts": list(Account.objects.filter(ledger=ledger).order_by("code").values()),
            "periods": list(Period.objects.filter(ledger=ledger).order_by("-start").values()),
            "rules": list(Rule.objects.filter(ledger=ledger).order_by("kind", "-starts").values()),
            "references": list(Reference.objects.filter(ledger=ledger).annotate(
                center_active=F("center_scope__active"), center_site_id=F("center_scope__site_id"))
                .order_by("kind", "code", "-starts").values())}


@endpoint("POST", module=MODULE)
def account(request):
    user = request.user
    require_finance(user, "setup")
    data = body(request)
    code = string(data, "code", 24).upper()
    name = string(data, "name", 120)
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]*", code):
        raise Problem("invalid_code")
    kind = data.get("kind")
    detail, active = data.get("detail"), data.get("active", True)
    if kind not in ("asset", "liability", "equity", "revenue", "expense") or type(detail) is not bool or type(active) is not bool:
        raise Problem("invalid_account")
    with transaction.atomic():
        ledger = ledger_lock()
        existing = find(Account, ledger, data["id"]) if data.get("id") is not None else None
        parent = find(Account, ledger, data["parent_id"], detail=False, kind=kind) if data.get("parent_id") is not None else None
        if existing:
            structure_changed = (existing.code, existing.kind, existing.detail, existing.parent_id) != (
                code, kind, detail, parent.pk if parent else None)
            if structure_changed and ledger.activated_at:
                raise Problem("finance_account_structure_locked", 409)
            if structure_changed and (Line.objects.filter(account=existing).exists()
                    or Accrual.objects.filter(Q(account=existing) | Q(inventory_account=existing)).exists()):
                raise Problem("finance_account_structure_locked", 409)
            if existing.account_set.exists() and (detail or kind != existing.kind):
                raise Problem("invalid_parent")
        ancestor = parent
        while ancestor is not None:
            if ancestor == existing:
                raise Problem("invalid_parent")
            ancestor = ancestor.parent
        if Account.objects.filter(ledger=ledger, code=code).exclude(pk=existing.pk if existing else None).exists():
            raise Problem("finance_duplicate_code", 409)
        row = existing or Account(ledger=ledger)
        if row.pk and not active and (Accrual.objects.filter(Q(account=row) | Q(inventory_account=row), remaining__gt=0).exists()):
            raise Problem("finance_open_accrual_account", 409)
        row.code, row.name, row.kind, row.parent, row.detail, row.active = code, name, kind, parent, detail, active
        if row.control and not detail:
            raise Problem("invalid_account")
        row.save()
        audit(user, "accounting.account", f"{row.code} · {name}")
        return {"id": row.pk}, 200 if existing else 201


@endpoint("POST", module=MODULE)
def rule(request):
    user = request.user
    require_finance(user, "setup")
    data = body(request)
    kind = data.get("kind")
    starts = day_field(data, "starts")
    if kind not in KINDS:
        raise Problem("invalid_kind")
    with transaction.atomic():
        ledger = ledger_lock()
        if ledger.activated_at and (starts < ledger.cutoff or Posting.objects.filter(
                ledger=ledger, movement__kind=kind, movement__business_date__gte=starts).exclude(state="pending").exists()):
            raise Problem("finance_rule_history_locked", 409)
        source = Rule.objects.filter(ledger=ledger, kind="receipt", starts__lte=starts).order_by("-starts").first()
        if kind in ("receipt", "opening", "maint_issue", "transfer_out", "mfg_issue"):
            inventory = find(Account, ledger, data.get("inventory_id"), active=True, detail=True, kind="asset")
            offset = find(Account, ledger, data.get("offset_id"), active=True, detail=True,
                          kind={"opening": "equity", "maint_issue": "expense", "transfer_out": "asset", "mfg_issue": "asset"}.get(kind, "liability"))
        else:
            if source is None:
                raise Problem("finance_rule_missing", 409)
            inventory, offset = source.inventory, source.offset
        variance = find(Account, ledger, data.get("variance_id"), active=True, detail=True,
                        kind="expense") if kind in ("return", "transfer_out", "mfg_issue") else None
        if kind in ("transfer_out", "mfg_issue") and inventory.pk == offset.pk:
            raise Problem("invalid_offset_id", field="offset_id", reason="invalid_choice")
        row, created = Rule.objects.get_or_create(ledger=ledger, kind=kind, starts=starts,
            defaults={"inventory": inventory, "offset": offset, "variance": variance, "created_by": user.get_username()})
        if not created:
            raise Problem("finance_duplicate_rule", 409)
        Account.objects.filter(pk__in=[inventory.pk, offset.pk, variance.pk if variance else None]).update(control=True)
        audit(user, "accounting.rule.publish", f"{kind} / {starts} / #{row.pk}")
        return {"id": row.pk}, 201


@endpoint("POST", module=MODULE)
def reference(request):
    user = request.user
    require_finance(user, "setup")
    data = body(request)
    kind = data.get("kind")
    if kind not in ("cost_center", "tax"):
        raise Problem("invalid_kind")
    if kind == "cost_center":
        raise Problem("use_cost_center_setup", 409)
    code = string(data, "code", 24).upper()
    starts = day_field(data, "starts") if kind == "tax" else None
    rate = decimal_value(data.get("rate"), 3, 4, "rate") if kind == "tax" else None
    if rate is not None and rate > 100:
        raise Problem("invalid_rate")
    with transaction.atomic():
        ledger = ledger_lock()
        row, created = Reference.objects.get_or_create(ledger=ledger, kind=kind, code=code, starts=starts,
                           defaults={"name": string(data, "name", 120), "rate": rate})
        if not created:
            raise Problem("finance_duplicate_code", 409)
        audit(user, "accounting.reference", f"{kind} / {code}")
        return {"id": row.pk}, 201


@endpoint("POST", module=MODULE)
def periods(request):
    require_finance(request.user, "setup")
    start = day_field(body(request), "start", error="invalid_period_start")
    if start.day != 1:
        raise Problem("invalid_period_start")
    with transaction.atomic():
        ledger = ledger_lock()
        row, created = Period.objects.get_or_create(ledger=ledger, start=start, defaults={"end": month_end(start)})
        if created:
            audit(request.user, "accounting.period.open", start.isoformat())
        return {"id": row.pk}, 201 if created else 200


@endpoint("POST", module=MODULE)
def close(request, pk):
    require_finance(request.user, "close")
    with transaction.atomic():
        ledger = ledger_lock()
        period = find(Period, ledger, pk)
        if not ledger.activated_at:
            raise Problem("finance_not_active", 409)
        if Posting.objects.filter(ledger=ledger, state="pending").filter(
                Q(movement__business_date__lte=period.end) | Q(sale__document__date__lte=period.end)).exists():
            raise Problem("finance_pending_postings", 409)
        if not period.closed_at:
            period.closed_at = timezone.now()
            period.save(update_fields=["closed_at"])
            audit(request.user, "accounting.period.close", period.start.isoformat())
        return {"closed_at": period.closed_at}


@endpoint("POST", module=MODULE)
def cutover(request):
    user = request.user
    require_finance(user, "setup")
    data = body(request)
    cutoff = day_field(data, "cutoff")
    with transaction.atomic():
        ledger = ledger_lock()
        if "token" not in data:
            return preview(ledger, cutoff)
        result = activate(ledger, cutoff, string(data, "token", 64), user)
        return {"entry_id": result.pk if result else None, "cutoff": cutoff}, 201


def entry_data(row):
    posting = Posting.objects.filter(entry=row).values("movement_id", "movement__receipt_id",
                                                        "movement__receipt__order_id", "sale__document_id").first()
    if row.kind in ("maint_issue", "maint_return") and posting:
        from inventory.models import Movement

        facts = Movement.objects.values_list("facts", flat=True).get(pk=posting["movement_id"])
        posting["work_order_id"] = facts["work_order_id"]
    if row.kind in ("ap_invoice", "ap_reverse"):
        invoice = SupplierInvoice.objects.filter(Q(entry=row) | Q(reversal_entry=row)).values_list("pk", flat=True).first()
        posting = {"ap_invoice_ids": [invoice]} if invoice else None
    elif row.kind in ("ap_payment", "ap_credit"):
        posting = {"ap_invoice_ids": list(SettlementAllocation.objects.filter(settlement__entry=row)
                                        .values_list("invoice_id", flat=True).order_by().distinct())}
    return {"id": row.pk, "number": row.number, "date": row.date.isoformat(), "source_date": row.source_date.isoformat() if row.source_date else None,
            "description": row.description, "kind": row.kind, "created_by": row.created_by, "posted_at": row.posted_at,
            "posted_by": row.posted_by, "posting_note": row.posting_note, "revision": row.revision,
            "reversal_of_id": row.reversal_of_id, "original_entry_id": row.original_entry_id,
            "rule_id": row.rule_id, "source": posting,
            "lines": [{"account_id": line.account_id, "code": line.account.code, "site_id": line.site_id,
                       "cost_center_id": line.cost_center_id, "debit": str(max(line.amount, Decimal("0.00"))),
                       "credit": str(max(-line.amount, Decimal("0.00")))}
                      for line in row.lines.select_related("account").order_by("id")]}


def manual_values(ledger, data):
    lines = data.get("lines")
    if not isinstance(lines, list) or not 2 <= len(lines) <= 100:
        raise Problem("invalid_lines")
    values = []
    for index, line in enumerate(lines):
        with field_path(f"lines.{index}"):
            if not isinstance(line, dict):
                raise Problem("invalid_lines")
            account = find(Account, ledger, line.get("account_id"), active=True, detail=True, control=False)
            debit = decimal_value(line.get("debit"), 15, 2, "debit")
            credit = decimal_value(line.get("credit"), 15, 2, "credit")
            if bool(debit) == bool(credit):
                raise Problem("finance_invalid_line_direction", fields={"debit": ["line_direction"], "credit": ["line_direction"]})
            site_id = site(line)
            center = center_for_site(line.get("cost_center_id"), site_id)
            values.append((account, debit - credit, site_id, center))
    return values


@endpoint("GET", "POST", module=MODULE)
def journals(request):
    user = request.user
    require_finance(user)
    ledger = Ledger.objects.get(pk=1)
    if request.method == "GET":
        rows = Entry.objects.filter(ledger=ledger).order_by("-id")
        before = request.GET.get("before")
        if before is not None:
            if not before.isascii() or not before.isdecimal() or len(before) > 18:
                raise Problem("invalid_cursor")
            rows = rows.filter(pk__lt=int(before))
        if "page_size" in request.GET:
            from backbone.services.pagination import operational_page
            return {**operational_page(request,rows,entry_data,['description', 'kind'])}
        page = list(rows[:101])
        return {"items": [entry_data(row) for row in page[:100]], "next_before": page[99].pk if len(page) > 100 else None}
    require_finance(user, "manual")
    data = body(request)
    description = string(data, "description", 1000)
    day = day_field(data, "date")

    def create():
        current = ledger_lock()
        values = manual_values(current, data)
        row = Entry.objects.create(ledger=current, kind="manual", date=day, description=description,
                                   created_by=user.get_username())
        write_lines(row, values)
        audit(user, "accounting.journal.create", f"{row.number} · {description}")
        return entry_data(row)

    return once(request, "accounting.journal.create", data, create)


@endpoint("GET", "PATCH", module=MODULE)
def journal(request, pk):
    user = request.user
    require_finance(user)
    if request.method == "GET":
        return entry_data(find(Entry, Ledger.objects.get(pk=1), pk))
    require_finance(user, "manual")
    data = body(request)
    description = string(data, "description", 1000)
    with transaction.atomic():
        ledger = ledger_lock()
        row = find(Entry, ledger, pk, kind="manual", posted_at__isnull=True, reversal_of__isnull=True)
        if identifier(data.get("revision"), "revision") != row.revision:
            raise Problem("revision_conflict", 409)
        values = manual_values(ledger, data)
        row.date, row.description = day_field(data, "date"), description
        row.revision += 1
        row.save(update_fields=["date", "description", "revision"])
        row.lines.all().delete()
        write_lines(row, values)
        audit(user, "accounting.journal.edit", f"{row.number} · {description}")
        return entry_data(row)


@endpoint("POST", module=MODULE)
def manual_post(request, pk):
    user = request.user
    require_finance(user, "manual.post")
    data = body(request)
    with transaction.atomic():
        ledger = ledger_lock()
        row = find(Entry, ledger, pk, kind="manual")
        if row.posted_at:
            raise Problem("finance_already_posted", 409)
        if identifier(data.get("revision"), "revision") != row.revision:
            raise Problem("revision_conflict", 409)
        if not ledger.activated_at or row.date < ledger.cutoff:
            raise Problem("finance_before_cutoff", 409)
        if row.reversal_of_id and row.date < row.reversal_of.date:
            raise Problem("finance_before_original", 409)
        period = period_for(ledger, row.date)
        if row.lines.filter(Q(account__active=False) | Q(account__detail=False) | Q(account__control=True)).exists():
            raise Problem("finance_account_unavailable", 409)
        if not row.reversal_of_id:
            for line in row.lines.exclude(cost_center_id=None):
                center_for_site(line.cost_center_id, line.site_id, legacy=True)
        row.period, row.posted_at, row.posted_by = period, timezone.now(), user.get_username()
        row.revision += 1
        row.save(update_fields=["period", "posted_at", "posted_by", "revision"])
        audit(user, "accounting.journal.post", f"{row.number} · {row.description}")
        return entry_data(row)


@endpoint("POST", module=MODULE)
def reverse_manual(request, pk):
    user = request.user
    require_finance(user, "manual")
    data = body(request)
    reason = string(data, "description", 1000)
    day = day_field(data, "date")

    def create():
        ledger = ledger_lock()
        original = find(Entry, ledger, pk, kind="manual", posted_at__isnull=False)
        if day < original.date or Entry.objects.filter(reversal_of=original).exists():
            raise Problem("finance_invalid_reversal", 409)
        row = Entry.objects.create(ledger=ledger, kind="manual", date=day, description=reason,
                                   created_by=user.get_username(), reversal_of=original)
        write_lines(row, [(line.account, -line.amount, line.site_id, line.cost_center)
                          for line in original.lines.select_related("account", "cost_center").order_by("id")])
        audit(user, "accounting.journal.reverse", f"{row.number} / {original.number} · {reason}")
        return entry_data(row)

    return once(request, "accounting.journal.reverse", {**data, "id": pk}, create)


def posting_data(row):
    source = row.sale.document if row.sale_id else row.movement
    return {"id": row.pk, "movement_id": row.movement_id, "sale_id": source.pk if row.sale_id else None,
            "sale_number": source.number if row.sale_id else None, "entry_id": row.entry_id,
            "state": row.state, "error": row.error, "kind": source.kind,
            "source_date": source.date if row.sale_id else source.business_date}


@endpoint("GET", module=MODULE)
def queue(request):
    require_finance(request.user)
    rows = Posting.objects.select_related("movement", "sale__document").order_by("-id")
    state = request.GET.get("state", "pending")
    if state != "all":
        rows = rows.filter(state=state)
    count = rows.count()
    before = request.GET.get("before")
    if before is not None:
        if not before.isascii() or not before.isdecimal() or len(before) > 18:
            raise Problem("invalid_cursor")
        rows = rows.filter(pk__lt=int(before))
    page = list(rows[:101])
    if "page_size" in request.GET:
        from backbone.services.pagination import operational_page
        return {**operational_page(request,rows,posting_data,['state', 'error'])}
    return {"items": [posting_data(row) for row in page[:100]], "count": count,
            "next_before": page[99].pk if len(page) > 100 else None}


@endpoint("POST", module=MODULE)
def retry(request, pk):
    user = request.user
    require_finance(user, "post")
    data = body(request)
    override = day_field(data, "date") if data.get("date") else None
    note = string(data, "reason", 1000, required=override is not None)
    with transaction.atomic():
        ledger = ledger_lock()
        row = find(Posting, ledger, pk)
        result = process(row, user.get_username(), override, note)
        audit(user, "accounting.posting.retry", f"{row.sale.document.number if row.sale_id else f'Movement #{row.movement_id}'} · {result.state} · {note}")
        return posting_data(result)


@endpoint("GET", module=MODULE)
def reports(request, kind):
    require_finance(request.user)
    ledger = Ledger.objects.get(pk=1)
    if kind == "grni":
        rows = Accrual.objects.filter(ledger=ledger).select_related("receipt_movement__receipt__order")
        invoiced = dict(InvoiceMatch.objects.filter(invoice__reversal_entry__isnull=True).values("accrual_id").annotate(total=Sum("amount"))
                        .values_list("accrual_id", "total"))
        return {"items": [{
            "movement_id": row.receipt_movement_id, "entry_id": row.entry_id, "site_id": row.receipt_movement.site_id,
            "value": str(row.remaining), "account_id": row.account_id,
            "original_value": str(row.receipt_movement.value), "invoiced_value": str(invoiced.get(row.pk, Decimal("0.00"))),
            "returned_value": str(row.receipt_movement.value - row.remaining - invoiced.get(row.pk, Decimal("0.00"))),
            "receipt": row.receipt_movement.receipt.number if row.receipt_movement.receipt_id else None,
            "order": row.receipt_movement.receipt.order.number if row.receipt_movement.receipt_id else None,
            "supplier": row.receipt_movement.receipt.order.snapshot["supplier"]["name"] if row.receipt_movement.receipt_id else None,
        } for row in rows.order_by("receipt_movement_id")
          if row.remaining > 0 or row.pk in invoiced or request.GET.get("include_closed") == "1"]}
    start, end = day_field(request.GET, "start"), day_field(request.GET, "end")
    if end < start:
        raise Problem("invalid_report_range")
    rows = Line.objects.filter(entry__ledger=ledger, entry__posted_at__isnull=False, entry__date__lte=end)
    if kind == "ledger":
        account_id = request.GET.get("account_id")
        if not account_id or not account_id.isascii() or not account_id.isdecimal() or len(account_id) > 18:
            raise Problem("invalid_account")
        rows = rows.filter(account_id=int(account_id))
        opening = rows.filter(entry__date__lt=start).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        return {"opening": str(opening), "items": [{"entry_id": row.entry_id, "number": row.entry.number,
            "date": row.entry.date, "description": row.entry.description, "site_id": row.site_id, "amount": str(row.amount)}
            for row in rows.filter(entry__date__gte=start).select_related("entry").order_by("entry__date", "entry_id", "pk")]}
    if kind != "trial":
        raise Problem("not_found", 404)
    amounts = {r["account_id"]: r for r in rows.values("account_id").annotate(
        opening=Sum("amount", filter=Q(entry__date__lt=start)),
        debit=Sum("amount", filter=Q(entry__date__gte=start, amount__gt=0)),
        credit=Sum("amount", filter=Q(entry__date__gte=start, amount__lt=0)))}
    result = []
    for account in Account.objects.filter(ledger=ledger, pk__in=amounts).order_by("code"):
        data = amounts[account.pk]
        opening, debit, signed_credit = (data[k] or Decimal("0.00") for k in ("opening", "debit", "credit"))
        result.append({"id": account.pk, "code": account.code, "name": account.name, "opening": str(opening),
                       "debit": str(debit), "credit": str(abs(signed_credit)), "closing": str(opening + debit + signed_credit)})
    return {"items": result}
