import calendar
import hashlib
import json
from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from backbone.access import audit
from backbone.http import Problem
from backbone.extensions import posting_handler
from backbone.registry import enabled_modules
from inventory.models import Movement
from supply.accounting import KINDS as TRANSFER_KINDS, effect as transfer_effect, transit_value


def receipt_reference(move):
    if not move.receipt_id:
        return {"receipt": None, "order": None, "supplier": None}
    order = move.receipt.order
    return {"receipt": move.receipt.number, "order": order.number,
            "supplier": order.snapshot["supplier"]["name"]}
from .models import Account, Accrual, Entry, Ledger, Line, Period, Posting, Reference, Rule


def ledger_lock():
    ledger, _ = Ledger.objects.select_for_update().get_or_create(pk=1)
    return ledger


def period_for(ledger, day):
    period = Period.objects.select_for_update().filter(ledger=ledger, start__lte=day, end__gte=day).first()
    if period is None:
        raise Problem("finance_period_missing", 409)
    if period.closed_at:
        raise Problem("finance_period_closed", 409)
    return period


def rule_for(ledger, kind, day):
    rule = Rule.objects.filter(ledger=ledger, kind=kind, starts__lte=day).order_by("-starts").first()
    if rule is None:
        raise Problem("finance_rule_missing", 409)
    return rule


def write_lines(entry, values):
    values = [(account, amount, site, center) for account, amount, site, center in values if amount]
    if not values or sum(amount for _, amount, _, _ in values) or any(abs(amount) >= 10**24 for _, amount, _, _ in values):
        raise Problem("finance_unbalanced", 409)
    for account, _, _, _ in values:
        if not account.active or not account.detail or account.ledger_id != entry.ledger_id:
            raise Problem("finance_account_unavailable", 409)
    Line.objects.bulk_create([Line(entry=entry, account=account, amount=amount, site_id=site, cost_center=center)
                              for account, amount, site, center in values])


def posted_entry(ledger, period, day, user, description, kind, values, **extra):
    entry = Entry.objects.create(ledger=ledger, period=period, date=day, description=description,
                                 kind=kind, created_by=user, posted_by=user, posted_at=timezone.now(), **extra)
    write_lines(entry, values)
    return entry


def effect(ledger, movement):
    value = movement.value
    if movement.kind == "maint_issue":
        rule = rule_for(ledger, movement.kind, movement.business_date)
        return rule, [(rule.inventory, value), (rule.offset, -value)], None
    if movement.kind == "maint_return":
        source = Posting.objects.filter(movement_id=movement.original_id, state__in=("posted", "zero")).select_related("entry__rule").first()
        if source is None:
            raise Problem("finance_original_not_posted", 409)
        if not value:
            return None, [], None
        rule = source.entry.rule
        return rule, [(rule.inventory, value), (rule.offset, -value)], None
    rule = rule_for(ledger, movement.kind, movement.business_date)
    if movement.kind in ("opening", "receipt"):
        return rule, [(rule.inventory, value), (rule.offset, -value)], None
    original = Accrual.objects.filter(ledger=ledger, receipt_movement_id=movement.original_id).first()
    if original is None:
        raise Problem("finance_original_not_posted", 409)
    historical = Decimal(movement.facts["original_receipt_value"])
    if historical > original.remaining:
        raise Problem("finance_accrual_insufficient", 409)
    billed = sum((Decimal(part["amount"]) for part in movement.facts.get("ap_return_parts", [])), Decimal(0))
    difference = -value - historical - billed
    if difference and rule.variance_id is None:
        raise Problem("finance_rule_missing", 409)
    values = [(original.inventory_account, value), (original.account, historical)]
    if billed:
        from .payables import control

        values.append((control(ledger, "AP-RETURNS", "asset"), billed))
    if difference:
        values.append((rule.variance, difference))
    return rule, values, original


def process(row, actor, override=None, note=""):
    if row.state != "pending":
        return row
    try:
        with transaction.atomic():
            row = Posting.objects.select_for_update(of=("self",)).select_related("movement", "sale", "ledger").get(pk=row.pk)
            if row.state != "pending":
                return row
            if row.sale_id:
                from sales.ledger import process_sales

                return process_sales(row, actor, override, note)
            movement = row.movement
            ledger = row.ledger
            if movement.facts.get("currency") != ledger.currency:
                raise Problem("finance_currency_invalid", 409)
            day = override or movement.business_date
            if day < ledger.cutoff or day < movement.business_date:
                raise Problem("finance_before_cutoff", 409)
            if movement.kind == "maint_return" and Posting.objects.filter(
                    movement_id=movement.original_id, entry__date__gt=day).exists():
                raise Problem("finance_before_original", 409)
            period = period_for(ledger, day)
            handler = transfer_effect if movement.kind in TRANSFER_KINDS else posting_handler(movement.kind)
            if handler:
                rule, values, original_entry = handler(ledger, movement, day)
                entry = posted_entry(ledger, period, day, actor,
                                     f"{movement.kind} / stock movement #{movement.pk}", movement.kind, values,
                                     source_date=movement.business_date, rule=rule, posting_note=note,
                                     original_entry=original_entry) if any(value for _, value, _, _ in values) else None
            else:
                rule, values, original = effect(ledger, movement)
                entry = None
                if original and original.entry_id and day < original.entry.date:
                    raise Problem("finance_before_original", 409)
                if any(amount for _, amount in values):
                    center = Reference.objects.filter(pk=movement.facts.get("cost_center_id"), ledger=ledger,
                                                      kind="cost_center").first()
                    entry = posted_entry(ledger, period, day, actor, f"{movement.kind} / stock movement #{movement.pk}",
                                         movement.kind, [(account, amount, movement.site_id, center) for account, amount in values],
                                         source_date=movement.business_date, rule=rule, posting_note=note,
                                         original_entry=original.entry if original else None)
                if original:
                    original.remaining -= Decimal(movement.facts["original_receipt_value"])
                    original.save(update_fields=["remaining"])
            if movement.kind == "receipt":
                Accrual.objects.create(ledger=ledger, receipt_movement=movement, account=rule.offset,
                                       inventory_account=rule.inventory, entry=entry, remaining=movement.value)
            row.entry, row.state, row.error = entry, "posted" if entry else "zero", ""
            row.save(update_fields=["entry", "state", "error"])
    except Problem as failure:
        row.error = failure.code
        row.save(update_fields=["error"])
    return row


def observe(ledger, movement):
    if not ledger.activated_at:
        return
    row = Posting.objects.create(ledger=ledger, movement=movement)
    if "accounting" in enabled_modules():
        movement.posting = process(row, "system")


def opening_snapshot(ledger, cutoff):
    movement_rows = Movement.objects.filter(business_date__lt=cutoff).order_by("pk")
    stock = defaultdict(Decimal)
    outstanding = {}
    transit = {}
    ids = []
    for move in movement_rows:
        ids.append(move.pk)
        stock[move.site_id] += move.value
        if move.kind == "transfer_out":
            transit[move.pk] = [move.site_id, transit_value(move)]
        elif move.kind in TRANSFER_KINDS:
            if move.original_id not in transit:
                raise Problem("finance_cutoff_source_order", 409)
            transit[move.original_id][1] -= transit_value(move)
        elif move.kind == "receipt":
            outstanding[move.pk] = [move.site_id, move.value]
        elif move.original_id:
            if move.original_id not in outstanding:
                raise Problem("finance_cutoff_source_order", 409)
            outstanding[move.original_id][1] -= Decimal(move.facts["original_receipt_value"])
    if any(value < 0 for value in stock.values()) or any(value < 0 for _, value in (*outstanding.values(), *transit.values())):
        raise Problem("finance_cutoff_invalid_balance", 409)
    return stock, outstanding, transit, ids


def preview(ledger, cutoff):
    stock, outstanding, transit, ids = opening_snapshot(ledger, cutoff)
    accounts = rule_for(ledger, "opening", cutoff)
    receipt_rule = rule_for(ledger, "receipt", cutoff)
    receipts = Movement.objects.filter(pk__in=[pk for pk, (_, value) in outstanding.items() if value]).select_related("receipt__order").in_bulk()
    result = {"cutoff": cutoff.isoformat(), "inventory": [
        {"site_id": site, "value": str(value)} for site, value in sorted(stock.items())],
        "grni": [{"movement_id": pk, "site_id": site, "value": str(value), **receipt_reference(receipts[pk])}
                 for pk, (site, value) in outstanding.items() if value],
        "opening_rule_id": accounts.pk, "receipt_rule_id": receipt_rule.pk, "movement_count": len(ids)}
    if transit:
        transit_rule = rule_for(ledger, "transfer_out", cutoff)
        result["in_transit"] = [{"movement_id": pk, "site_id": site, "value": str(value)}
                                for pk, (site, value) in transit.items() if value]
        result["transit_account_id"] = transit_rule.offset_id
    token = hashlib.sha256(json.dumps([result, ids], sort_keys=True).encode()).hexdigest()
    return {**result, "token": token}


def activate(ledger, cutoff, token, user):
    if ledger.activated_at:
        raise Problem("finance_already_active", 409)
    if preview(ledger, cutoff)["token"] != token:
        raise Problem("finance_preview_changed", 409)
    period = period_for(ledger, cutoff)
    stock, outstanding, transit, ids = opening_snapshot(ledger, cutoff)
    opening_rule = rule_for(ledger, "opening", cutoff)
    receipt_rule = rule_for(ledger, "receipt", cutoff)
    offset = opening_rule.offset
    inventory = opening_rule.inventory
    if inventory.pk != receipt_rule.inventory_id:
        raise Problem("finance_cutoff_inventory_account_mismatch", 409)
    values = []
    for site, amount in stock.items():
        values.extend([(inventory, amount, site, None), (offset, -amount, site, None)])
    by_site = defaultdict(Decimal)
    for site, amount in outstanding.values():
        by_site[site] += amount
    for site, amount in by_site.items():
        values.extend([(offset, amount, site, None), (receipt_rule.offset, -amount, site, None)])
    if transit:
        transit_account = rule_for(ledger, "transfer_out", cutoff).offset
        for site, amount in transit.values():
            values.extend([(transit_account, amount, site, None), (offset, -amount, site, None)])
    entry = posted_entry(ledger, period, cutoff, user.get_username(), "Approved inventory and GRNI opening",
                         "cutover", values, rule=opening_rule) if any(amount for _, amount, _, _ in values) else None
    Accrual.objects.bulk_create([Accrual(ledger=ledger, receipt_movement_id=pk, remaining=amount,
        account=receipt_rule.offset, inventory_account=inventory, entry=entry)
        for pk, (_, amount) in outstanding.items()])
    Posting.objects.bulk_create([Posting(ledger=ledger, movement_id=pk, state="opening") for pk in ids])
    ledger.cutoff, ledger.activated_at, ledger.activated_by = cutoff, timezone.now(), user.get_username()
    ledger.save(update_fields=["cutoff", "activated_at", "activated_by"])
    Posting.objects.bulk_create([Posting(ledger=ledger, movement_id=pk)
        for pk in Movement.objects.filter(business_date__gte=cutoff).values_list("pk", flat=True)])
    for row in Posting.objects.filter(ledger=ledger, state="pending").order_by("movement_id").iterator(chunk_size=200):
        process(row, "system")
    audit(user, "accounting.cutover", cutoff.isoformat())
    return entry


def month_end(day):
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])
