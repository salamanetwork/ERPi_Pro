from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from accounting.services import ledger_lock, rule_for
from backbone.http import Problem, identifier, string
from inventory.stock import business_date, prorate, record
from supply.reservations import available, held, release_owner, reserve

from .bills import effective_bills, get, material_location, qty, scaled
from .models import Activity, Batch, ExceptionRequest, Material

ZERO = Decimal(0)


def owner(material):
    return f"manufacturing.material:{material.pk}"


def total(rows, field="quantity"):
    return rows.aggregate(total=Sum(field))["total"] or ZERO


def consumed(material):
    rows = Activity.objects.filter(material=material)
    return total(rows.filter(action="issue")) - total(rows.filter(action="return"))


def usable(material):
    return consumed(material)-total(Activity.objects.filter(material=material,action="scrap"))


def reserve_material(order, line):
    key = owner(line)
    needed = line.required - usable(line) - held(key)
    if needed > 0:
        quantity = min(needed, available(order.site_id, line.item_id, line.source_id))
        if quantity > 0:
            reserve(order.site_id, line.item_id, line.source_id, quantity, owner_key=key)


def reserve_materials(order):
    for line in order.materials.all():
        reserve_material(order, line)


def change_source(order, line, data):
    new_source = material_location(data, order.site_id)
    previous = line.source_id
    if previous != new_source.pk:
        release_owner(owner(line))
        line.source = new_source
        line.save(update_fields=["source"])
        reserve_material(order, line)
    return f"material #{line.pk} · location #{previous} → #{new_source.pk}"


def release(order, settings):
    bill = effective_bills(order.site_id, business_date(settings, timezone.now())).get(order.item_id)
    if not bill:
        raise Problem("mfg_bom_missing", 409)
    rows = []
    for part in bill.components:
        source_id = part.get("source_id") or order.source_id
        source = material_location({"source_id": source_id}, order.site_id)
        rows.append(Material(order=order, item_id=part["item_id"], source=source, snapshot=part,
                             required=scaled(part, order.quantity, bill.quantity)))
    Material.objects.bulk_create(rows)
    order.bill = bill
    order.state = "released"
    reserve_materials(order)


def movement(order, user, settings, kind, item, location, quantity, value, *, facts=None, original=None, hold=None):
    ledger = ledger_lock()
    if not ledger.activated_at:
        raise Problem("finance_not_active", 409)
    first = order.activities.order_by("pk").values_list("movement__facts", flat=True).first()
    rule_id = (first["posting_rule_id"] if first else
               rule_for(ledger, "mfg_issue", business_date(settings, timezone.now())).pk)
    move = record(user, settings, item, location, kind, quantity, value,
                  {"manufacturing_order_id":order.pk, "posting_rule_id":rule_id, **(facts or {})},
                  original=original, hold_owner=hold, moving_average=kind=="mfg_issue")
    if move.posting.state not in ("posted", "zero"):
        raise Problem(move.posting.error or "mfg_posting_required", 409)
    return move


def material_for(order, data):
    return get(order.materials.select_related("item", "source"), identifier(data.get("material_id"), "material_id"))


def original_for(order, data):
    return get(order.activities.filter(action="issue").select_related("material__item", "movement__location"),
               identifier(data.get("original_id"), "original_id"))


def issue(order, user, settings, material, amount, reason="", approved=False):
    if consumed(material) + amount > material.required and not approved:
        raise Problem("mfg_approval_required", 409)
    move = movement(order, user, settings, "mfg_issue", material.item, material.source, -amount, ZERO, hold=owner(material))
    Activity.objects.create(order=order, material=material, movement=move, action="issue",
                            quantity=amount, value=-move.value, reason=reason)


def against_issue(order, user, settings, original, amount, reason, *, scrap=False):
    used = total(original.followups.all())
    if used + amount > original.quantity:
        raise Problem("mfg_issue_balance", 409)
    value = prorate(original.value, used+amount, original.quantity) - total(original.followups.all(), "value")
    mode = "scrap" if scrap else "return"
    move = movement(order, user, settings, "mfg_"+mode, original.material.item, original.movement.location,
                    ZERO if scrap else amount, ZERO if scrap else value, original=original.movement,
                    facts={"scrap_value":str(value)} if scrap else None)
    Activity.objects.create(order=order, material=original.material, movement=move, original=original, action=mode,
                            quantity=amount, value=value, reason=reason)


def request_exception(order, user, data):
    kind = data.get("kind")
    amount, reason = qty(data), string(data, "reason", 500)
    original = None
    if kind == "scrap":
        original = original_for(order, data)
        material = original.material
        if total(original.followups.all()) + amount > original.quantity:
            raise Problem("mfg_issue_balance", 409)
    elif kind == "extra":
        material = material_for(order, data)
        if amount + consumed(material) <= material.required:
            raise Problem("mfg_exception_not_needed", 409)
    else:
        raise Problem("invalid_kind")
    ExceptionRequest.objects.create(order=order, material=material, original=original, kind=kind, quantity=amount,
                                    reporter=user, reason=reason)
    return reason


def decide(order, user, settings, data):
    row = get(order.exceptions.select_related("material__item","original__movement","original__material__item"),
              identifier(data.get("exception_id"), "exception_id"))
    if row.reporter_id == user.pk:
        raise Problem("mfg_separate_reviewer", 403)
    if row.state != "pending":
        raise Problem("mfg_invalid_state", 409)
    decision = data.get("decision")
    reason = string(data, "reason", 500)
    if decision == "approve":
        if row.kind == "extra":
            issue(order, user, settings, row.material, row.quantity, reason, approved=True)
        else:
            against_issue(order, user, settings, row.original, row.quantity, reason, scrap=True)
    elif decision != "dismiss":
        raise Problem("invalid_action")
    row.state = "approved" if decision=="approve" else "dismissed"
    row.reviewer, row.decision_reason = user, reason
    row.save(update_fields=["state","reviewer","decision_reason"])
    return reason


def receive(order, user, data):
    amount = qty(data)
    if amount + total(order.batches.all()) > order.quantity:
        raise Problem("mfg_output_excess", 409)
    Batch.objects.create(order=order, quantity=amount, received_by=user)


def material_cost(order):
    return (total(order.activities.filter(action="issue"), "value") -
            total(order.activities.filter(action__in=("return","scrap")), "value"))


def close(order, user, settings, data):
    if order.exceptions.filter(state="pending").exists():
        raise Problem("mfg_pending_exceptions", 409)
    batches = list(order.batches.order_by("pk"))
    accepted = sum((batch.quantity for batch in batches), ZERO)
    cost = material_cost(order)
    if not accepted or cost < 0:
        raise Problem("mfg_close_balance", 409)
    reason = ""
    if accepted != order.quantity:
        reason = string(data, "reason", 500)
    for line in order.materials.all():
        actual = usable(line)
        minimum = prorate(line.required, accepted, order.quantity, Decimal("0.000000001"))
        if actual < minimum:
            raise Problem("mfg_material_short", 409)
    cumulative = previous = ZERO
    for batch in batches:
        cumulative += batch.quantity
        allocated = prorate(cost, cumulative, accepted)
        move = movement(order, user, settings, "mfg_output", order.item, order.destination, batch.quantity, allocated-previous,
                        facts={"batch_id":batch.pk})
        batch.movement = move
        batch.save(update_fields=["movement"])
        previous = allocated
    for line in order.materials.all():
        release_owner(owner(line))
    order.state = "closed"
    return reason


def cancel(order, data):
    if order.batches.exists() or order.exceptions.filter(state="pending").exists() or order.activities.filter(action="scrap").exists():
        raise Problem("mfg_invalid_state", 409)
    if any(consumed(line) for line in order.materials.all()):
        raise Problem("mfg_return_before_cancel", 409)
    reason = string(data, "reason", 500)
    for line in order.materials.all():
        release_owner(owner(line))
    order.state = "cancelled"
    return reason
