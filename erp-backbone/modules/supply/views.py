from decimal import Decimal

from django.db import transaction
from django.db.models import CharField, Exists, F, OuterRef, Q, Sum, Value
from django.db.models.functions import Cast, Concat, LPad
from django.utils import timezone

from accounting.views import day_field
from backbone.access import audit, has_permission, scopes, visible
from backbone.http import Problem, body, endpoint, identifier, once, require, string
from backbone.registry import enabled_modules
from backbone.services.pagination import paginate, positive_filter, search_rows
from backbone.validation import field_path
from inventory.models import Item, Location
from inventory.stock import locked_site, prorate, quantity_in_base, record
from purchasing.money import decimal_value
from .accounting import loss_movement
from .models import Discrepancy, Reservation, Shipment, ShipmentLine, Transfer, TransferLine
from .reservations import reserve, total

MODULE = "supply"
ZERO = Decimal(0)


def location(key, field, site=None):
    rows = Location.objects.filter(warehouse__active=True, warehouse__site__active=True).select_related("warehouse")
    if site is not None:
        rows = rows.filter(warehouse__site_id=site)
    value = rows.filter(pk=identifier(key, field)).first()
    if value is None:
        raise Problem("invalid_" + field, field=field, reason="invalid_choice")
    return value


def quantity(data):
    value = decimal_value(data.get("quantity"), 19, 9, "quantity")
    return quantity_in_base(value, Decimal(1))


def rows_of(data):
    rows = data.get("lines")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 100 or not all(isinstance(row, dict) for row in rows):
        raise Problem("invalid_lines")
    return rows


def find(rows, pk):
    row = rows.filter(pk=pk).first()
    if row is None:
        raise Problem("not_found", 404)
    return row


def accessible(user):
    allowed = scopes(user, "supply.view")
    rows = Transfer.objects.all()
    return rows if None in allowed else rows.filter(Q(site_id__in=allowed) | Q(destination_id__in=allowed))


def stock_settings(row):
    return {site: locked_site(site) for site in sorted({row.site_id, row.destination_id})}


def shipment_line_data(line):
    return {"id": line.pk, "line_id": line.line_id, "item_id": line.line.item_id, "item": line.line.item.name,
            "quantity": str(line.quantity), "remaining": str(line.remaining), "value": str(line.value),
            "remaining_value": str(line.remaining_value), "movement_id": line.dispatch_id,
            "discrepancies": [discrepancy_data(value) for value in line.discrepancies.all()]}


def discrepancy_data(row):
    return {"id": row.pk, "kind": row.kind, "quantity": str(row.quantity), "state": row.state, "reason": row.reason,
            "decision_reason": row.decision_reason, "reporter": row.reporter.get_username(),
            "reviewer": row.reviewer.get_username() if row.reviewer_id else None, "movement_id": row.movement_id,
            "created_at": row.created_at.isoformat(), "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None}


def header(row):
    return {"id": row.pk, "number": row.number, "state": row.state, "site_id": row.site_id,
            "destination_id": row.destination_id, "revision": row.revision, "note": row.note}


def detail(row, user):
    from inventory.models import Movement

    result = header(row)
    lines = row.lines.select_related("item", "source__warehouse", "destination__warehouse", "reservation")
    stored = row.shipments.select_related("actor").prefetch_related(
        "lines__line__item", "lines__discrepancies__reporter", "lines__discrepancies__reviewer").order_by("pk")
    events = Movement.objects.filter(kind__in=("transfer_in", "transfer_back", "transfer_loss"),
        original_id__in=ShipmentLine.objects.filter(shipment__transfer=row).values("dispatch_id")).select_related("posting").order_by("pk")
    result.update(
        lines=[{"id": line.pk, "item_id": line.item_id, "item":line.item.name, "source_id": line.source_id,
                "source": f"{line.source.warehouse.name} / {line.source.name}", "destination_id": line.destination_id,
                "destination": f"{line.destination.warehouse.name} / {line.destination.name}", "quantity": str(line.quantity),
                "dispatched": str(line.dispatched), "reserved": str(line.reservation.quantity if hasattr(line, "reservation") else ZERO)}
               for line in lines],
        shipments=[{"id": shipment.pk, "number": shipment.number, "carrier": shipment.carrier, "reference": shipment.reference,
                    "eta": shipment.eta.isoformat() if shipment.eta else None, "actor": shipment.actor.get_username(),
                    "dispatched_at": shipment.dispatched_at.isoformat(),
                    "lines": [shipment_line_data(line) for line in shipment.lines.all()]} for shipment in stored],
        events=[{"id": movement.pk, "kind": movement.kind, "actor": movement.actor, "reason": movement.facts.get("reason"),
                 "site_id": movement.site_id, "shipment_line_id": movement.facts["shipment_line_id"],
                 "quantity": movement.facts["transit_quantity"], "value": movement.facts["transit_value"],
                 "at":movement.occurred_at.isoformat(), "entry_id": movement.posting.entry_id if hasattr(movement, "posting") else None}
                for movement in events],
        actions=[action for action, permission, site in (
            ("prepare","supply.prepare",row.site_id), ("dispatch","supply.dispatch",row.site_id),
            ("receive","supply.receive",row.destination_id), ("settle","supply.settle",row.destination_id),
        ) if has_permission(user, permission, site) and (action != "settle" or has_permission(user, "accounting.post", None))],
    )
    return result


def apply_lines(row, data):
    seen = set()
    for index, value in enumerate(rows_of(data)):
        with field_path(f"lines.{index}"):
            source = location(value.get("source_id"), "source_id", row.site_id)
            destination = location(value.get("destination_id"), "destination_id", row.destination_id)
            item = Item.objects.filter(pk=identifier(value.get("item_id"), "item_id"), active=True).first()
            if source.pk == destination.pk or item is None:
                raise Problem("supply_invalid_line")
            key = (item.pk, source.pk, destination.pk)
            if key in seen:
                raise Problem("supply_duplicate_line")
            seen.add(key)
            TransferLine.objects.create(transfer=row, item=item, source=source, destination=destination, quantity=quantity(value))


def create_draft(user, data):
    source = identifier(data.get("site_id"), "site_id")
    destination = identifier(data.get("destination_id"), "destination_id")
    require(user, "supply.view", source)
    require(user, "supply.prepare", source)
    for site in sorted({source, destination}):
        locked_site(site)
    row = Transfer.objects.create(site_id=source, destination_id=destination,
                                  note=string(data, "note", 500, required=False))
    apply_lines(row, data)
    audit(user, "supply.transfer.create", row.number)
    return row


@endpoint("GET", "POST", module=MODULE)
def transfers(request):
    user = request.user
    require(user, "supply.view")
    if request.method == "GET":
        rows = accessible(user)
        if site := positive_filter(request.GET, "site"):
            rows = rows.filter(Q(site_id=site) | Q(destination_id=site))
        if state := request.GET.get("state"):
            rows = rows.filter(state=state)
        if q := request.GET.get("q", "").strip()[:120]:
            rows = rows.annotate(
                tr_code=Concat(Value("TR-"), LPad(Cast("pk", CharField()), 6, Value("0")), output_field=CharField())
            ).filter(
                Q(tr_code__icontains=q) | Q(note__icontains=q) | Q(site__name__icontains=q) | Q(destination__name__icontains=q)
            )
        if request.GET.get("discrepancy") == "pending":
            reports = Discrepancy.objects.filter(line__shipment__transfer_id=OuterRef("pk"), state="pending")
            rows = rows.filter(Exists(reports))
        return paginate(request, rows.order_by("-pk"), header)
    data = body(request)
    source = identifier(data.get("site_id"), "site_id")
    destination = identifier(data.get("destination_id"), "destination_id")
    require(user, "supply.view", source)
    require(user, "supply.prepare", source)

    def commit():
        return detail(create_draft(user, data), user)
    return once(request, "supply.transfer.create", data, commit)


@endpoint("GET", module=MODULE)
def transfer(request, pk):
    return detail(find(accessible(request.user), pk), request.user)


def check_revision(row, data):
    if identifier(data.get("revision"), "revision") != row.revision:
        raise Problem("revision_conflict", 409)


def downstream_state(row):
    open_shipments = ShipmentLine.objects.filter(shipment__transfer=row, remaining__gt=0)
    reserved = Reservation.objects.filter(transfer_line__transfer=row, quantity__gt=0).exists()
    if not reserved and not open_shipments.exists():
        return "received" if row.shipments.exists() else "cancelled"
    if row.shipments.exists():
        return "partial" if ShipmentLine.objects.filter(shipment__transfer=row, remaining__lt=F("quantity")).exists() else "in_transit"
    return "confirmed"


def confirm(row, user):
    if row.state != "draft":
        raise Problem("supply_invalid_state", 409)
    for line in row.lines.order_by("item_id", "source_id", "pk"):
        location(line.source_id, "source_id", row.site_id)
        location(line.destination_id, "destination_id", row.destination_id)
        reserve(row.site_id, line.item_id, line.source_id, line.quantity, transfer_line=line)
    row.state = "confirmed"


def cancel(row, data):
    if row.state in ("received", "cancelled"):
        raise Problem("supply_invalid_state", 409)
    reason = string(data, "reason", 500)
    Reservation.objects.filter(transfer_line__transfer=row).update(quantity=0)
    row.state = downstream_state(row)
    return reason


def dispatch(row, user, data, settings):
    if row.state not in ("confirmed", "in_transit", "partial"):
        raise Problem("supply_invalid_state", 409)
    shipment = Shipment.objects.create(transfer=row, actor=user,
        carrier=string(data, "carrier", 120, required=False), reference=string(data, "reference", 120, required=False),
        eta=day_field(data, "eta") if data.get("eta") else None)
    seen = set()
    for value in rows_of(data):
        key = identifier(value.get("line_id"), "line_id")
        if key in seen:
            raise Problem("supply_duplicate_line")
        seen.add(key)
        line = find(row.lines.select_related("item", "source", "reservation"), key)
        amount = quantity(value)
        if not hasattr(line, "reservation") or amount > line.reservation.quantity:
            raise Problem("supply_exceeds_reserved", 409)
        move = record(user, settings[row.site_id], line.item, line.source, "transfer_out", -amount, ZERO,
                      {"transfer_id": row.pk, "shipment_id": shipment.pk, "transfer_line_id": line.pk})
        ShipmentLine.objects.create(shipment=shipment, line=line, dispatch=move, quantity=amount, remaining=amount,
                                    value=-move.value, remaining_value=-move.value)
        line.dispatched += amount
        line.save(update_fields=["dispatched"])
    row.state = downstream_state(row)
    return shipment.number


def open_reported(line):
    return line.discrepancies.filter(state="pending").aggregate(quantity=Sum("quantity"))["quantity"] or ZERO


def allocate(line, qty):
    amount = prorate(line.value, line.quantity - line.remaining + qty, line.quantity) - (line.value - line.remaining_value)
    line.remaining -= qty
    line.remaining_value -= amount
    line.save(update_fields=["remaining", "remaining_value"])
    return amount


def trace(row, line, quantity, value, reason=""):
    return {"transfer_id":row.pk, "shipment_id": line.shipment_id, "shipment_line_id": line.pk,
            "transit_quantity": str(quantity), "transit_value": str(value), "reason": reason}


def receive(row, user, data, settings, returned=False):
    if row.state not in ("in_transit", "partial"):
        raise Problem("supply_invalid_state", 409)
    shipment = find(row.shipments.all(), identifier(data.get("shipment_id"), "shipment_id"))
    reason = string(data, "reason", 500, required=returned)
    seen = set()
    for value in rows_of(data):
        key = identifier(value.get("line_id"), "line_id")
        if key in seen:
            raise Problem("supply_duplicate_line")
        seen.add(key)
        line = find(shipment.lines.select_related("line__item", "line__source", "line__destination", "dispatch"),
                    key)
        qty = quantity(value)
        if qty > line.remaining - open_reported(line):
            raise Problem("supply_exceeds_in_transit", 409)
        amount = allocate(line, qty)
        target, site, kind = ((line.line.source, row.site_id, "transfer_back") if returned else
                              (line.line.destination, row.destination_id, "transfer_in"))
        record(user, settings[site], line.line.item, target, kind, qty, amount,
               trace(row, line, qty, amount, reason), original=line.dispatch)
    row.state = downstream_state(row)
    return f"{shipment.number} · {reason}"


def report(row, user, data):
    line = find(ShipmentLine.objects.filter(shipment__transfer=row),
                identifier(data.get("shipment_line_id"), "shipment_line_id"))
    qty = quantity(data)
    kind = data.get("kind")
    if kind not in ("damage", "shortage"):
        raise Problem("invalid_kind")
    if qty > line.remaining - open_reported(line):
        raise Problem("supply_exceeds_in_transit", 409)
    record_row = Discrepancy.objects.create(line=line, quantity=qty, kind=kind, reporter=user,
                                           reason=string(data, "reason", 500))
    return f"#{record_row.pk} · {record_row.reason}"


def decide(row, user, data, settings):
    require(user, "accounting.post", None)
    report_row = find(Discrepancy.objects.filter(line__shipment__transfer=row).select_related(
        "line__dispatch", "line__line"), identifier(data.get("discrepancy_id"), "discrepancy_id"))
    if report_row.reporter_id == user.pk:
        raise Problem("supply_separate_approver", 403)
    if report_row.state != "pending":
        raise Problem("supply_invalid_state", 409)
    decision = data.get("decision")
    if decision not in ("approve", "dismiss"):
        raise Problem("invalid_action")
    reason = string(data, "reason", 500)
    line = report_row.line
    if decision == "approve":
        qty = report_row.quantity
        if qty > line.remaining:
            raise Problem("supply_exceeds_in_transit", 409)
        value = allocate(line, qty)
        report_row.movement = loss_movement(user, settings[row.destination_id], line, value,
                                            trace(row, line, qty, value, reason))
    report_row.state = "approved" if decision == "approve" else "dismissed"
    report_row.reviewer = user
    report_row.decision_reason = reason
    report_row.reviewed_at = timezone.now()
    report_row.save()
    row.state = downstream_state(row)
    return f"#{report_row.pk} · {report_row.state} · {reason}"


ACTIONS = {
    "edit": ("supply.prepare", False), "confirm": ("supply.prepare", False), "cancel": ("supply.prepare", False),
    "dispatch": ("supply.dispatch", False), "return": ("supply.dispatch", False), "receive": ("supply.receive", True),
    "report": ("supply.receive", True), "decide": ("supply.settle", True),
}


@endpoint("POST", module=MODULE)
def action(request, pk, operation):
    user, data = request.user, body(request)
    if operation not in ACTIONS:
        raise Problem("not_found", 404)
    permission, incoming = ACTIONS[operation]
    current = find(accessible(user), pk)
    require(user, permission, current.destination_id if incoming else current.site_id)
    if operation == "decide":
        require(user, "accounting.post", None)

    def commit():
        row = find(accessible(user).select_for_update(), pk)
        check_revision(row, data)
        settings = stock_settings(row)
        note = ""
        if operation == "edit":
            if row.state != "draft":
                raise Problem("supply_invalid_state", 409)
            row.lines.all().delete()
            row.note = string(data, "note", 500, required=False)
            apply_lines(row, data)
        elif operation == "confirm":
            confirm(row, user)
        elif operation == "cancel":
            note = cancel(row, data)
        elif operation == "dispatch":
            note = dispatch(row, user, data, settings)
        elif operation in ("receive", "return"):
            note = receive(row, user, data, settings, operation == "return")
        elif operation == "report":
            note = report(row, user, data)
        else:
            note = decide(row, user, data, settings)
        row.revision += 1
        row.save(update_fields=["state", "revision", "note"])
        audit(user, "supply.transfer." + operation, f"{row.number} · {note}")
        return detail(row, user)
    return once(request, "supply.transfer." + operation, {"id": pk, "data": data}, commit)


@endpoint("GET", module=MODULE)
def catalog(request):
    require(request.user, "supply.view")
    locations = Location.objects.filter(warehouse__active=True, warehouse__site__active=True).select_related("warehouse__site")
    allowed = scopes(request.user, "supply.view")
    if None not in allowed and not (allowed & scopes(request.user, "supply.prepare") or None in scopes(request.user, "supply.prepare")):
        locations = locations.filter(warehouse__site_id__in=allowed)
    return {"items": list(Item.objects.filter(active=True).order_by("code").values("id", "code", "name")),
            "locations": [{"id":row.pk, "site_id":row.warehouse.site_id, "site_name":row.warehouse.site.name, "name":f"{row.warehouse.name} / {row.name}"}
                          for row in locations.order_by("warehouse__site_id", "warehouse__name", "code")]}


@endpoint("GET", module=MODULE)
def shipments(request):
    user = request.user
    require(user, "supply.view")
    rows = Shipment.objects.filter(transfer__in=accessible(user))
    if site := positive_filter(request.GET, "site"):
        rows = rows.filter(Q(transfer__site_id=site) | Q(transfer__destination_id=site))
    remaining = ShipmentLine.objects.filter(shipment_id=OuterRef("pk"), remaining__gt=0)
    rows = search_rows(rows, request.GET, ("carrier", "reference")).annotate(in_transit=Exists(remaining))
    if state := request.GET.get("state"):
        if state not in ("in_transit", "received"):
            raise Problem("supply_invalid_state")
        rows = rows.filter(in_transit=state == "in_transit")
    return paginate(request, rows.order_by("-pk"), lambda row: {
        "id": row.pk, "number": row.number, "transfer_id": row.transfer_id, "carrier": row.carrier,
        "reference": row.reference, "eta": row.eta.isoformat() if row.eta else None,
        "dispatched_at": row.dispatched_at.isoformat(), "state": "in_transit" if row.in_transit else "received",
    })


@endpoint("GET", module=MODULE)
def reservations(request):
    user = request.user
    require(user, "supply.view")
    rows = visible(Reservation.objects.filter(quantity__gt=0), user, "supply.view")
    if site := positive_filter(request.GET, "site"):
        rows = rows.filter(site_id=site)
    if location_id := positive_filter(request.GET, "location"):
        rows = rows.filter(location_id=location_id)
    if item := positive_filter(request.GET, "item"):
        rows = rows.filter(item_id=item)
    if order := positive_filter(request.GET, "order"):
        rows = rows.filter(order_id=order)
    rows = rows.select_related("item", "location__warehouse", "order", "transfer_line__transfer").order_by("-pk")
    return paginate(request, rows, lambda row: {
        "id":row.pk, "site_id":row.site_id, "item":row.item.name, "location":f"{row.location.warehouse.name} / {row.location.name}",
        "quantity":str(row.quantity),
        "order_id":row.order_id if has_permission(user, "sales.view", row.site_id) else None,
        "order":row.order.number if row.order_id and has_permission(user, "sales.view", row.site_id) else None,
        "transfer_id":row.transfer_line.transfer_id if row.transfer_line_id else None,
        "transfer":row.transfer_line.transfer.number if row.transfer_line_id else None,
    })


@endpoint("POST", module=MODULE)
def sales_reservation(request, pk, operation):
    from sales.models import Document
    from sales.views import order_progress

    user, data = request.user, body(request)
    if operation not in ("reserve", "release") or "sales" not in enabled_modules():
        raise Problem("not_found", 404)
    sales = visible(Document.objects.filter(kind="order"), user, "sales.view")
    current = find(sales, pk)
    require(user, "supply.prepare", current.site_id)
    require(user, "supply.view", current.site_id)

    def commit():
        row = find(sales.select_for_update(), pk)
        locked_site(row.site_id)
        if row.status != "confirmed":
            raise Problem("sales_order_not_confirmed", 409)
        live = Reservation.objects.filter(order=row)
        if operation == "release":
            row_reservation = find(live, identifier(data.get("reservation_id"), "reservation_id"))
            amount = quantity(data)
            if amount > row_reservation.quantity:
                raise Problem("supply_exceeds_reserved", 409)
            row_reservation.quantity -= amount
            row_reservation.save(update_fields=["quantity"])
        else:
            line_id = identifier(data.get("line_id"), "line_id")
            line = next((line for line in row.lines if line["id"] == line_id), None)
            progress = next((part for part in order_progress(row) if part["line_id"] == line_id), None)
            if not line or not progress:
                raise Problem("invalid_line_id")
            stock_location = location(data.get("location_id"), "location_id", row.site_id)
            amount = quantity(data)
            if amount > Decimal(progress["remaining"]) - total(live.filter(order_line=line_id)):
                raise Problem("supply_exceeds_order", 409)
            row_reservation = reserve(row.site_id, line["item_id"], stock_location.pk, amount, order=row, order_line=line_id)
        audit(user, "supply.sales." + operation, f"{row.number} · {amount} · #{row_reservation.pk}")
        return {"id": row_reservation.pk, "quantity": str(row_reservation.quantity)}
    return once(request, "supply.sales." + operation, {"id":pk, "data":data}, commit)


@endpoint("GET", module=MODULE)
def reconciliation(request):
    from accounting.models import Ledger, Line, Posting, Rule

    user = request.user
    require(user, "supply.view")
    require(user, "accounting.view", None)
    allowed = scopes(user, "supply.view")
    rows = ShipmentLine.objects.filter(remaining__gt=0)
    pending = Posting.objects.filter(movement__kind__in=("transfer_out", "transfer_in", "transfer_back", "transfer_loss"),
                                     entry__isnull=True, state="pending")
    def source_scope(sites):
        return (Q(movement__kind="transfer_out", movement__site_id__in=sites) |
                Q(movement__original__site_id__in=sites))
    journals = Line.objects.filter(entry__kind__in=("transfer_out", "transfer_in", "transfer_back", "transfer_loss", "cutover"),
        entry__posted_at__isnull=False, account__in=Rule.objects.filter(kind="transfer_out").values("offset_id"))
    if None not in allowed:
        rows = rows.filter(shipment__transfer__site_id__in=allowed)
        journals = journals.filter(site_id__in=allowed)
        pending = pending.filter(source_scope(allowed))
    if site := positive_filter(request.GET, "site"):
        rows = rows.filter(shipment__transfer__site_id=site)
        journals = journals.filter(site_id=site)
        pending = pending.filter(source_scope([site]))
    amount = rows.aggregate(value=Sum("remaining_value"))["value"] or ZERO
    posted = journals.aggregate(value=Sum("amount"))["value"] or ZERO
    active = Ledger.objects.filter(pk=1, activated_at__isnull=False).exists()
    return {"operational_value": format(amount, ".2f"), "posted_value": format(posted, ".2f") if active else None,
            "difference": format(amount-posted, ".2f") if active else None, "currency": "AED",
            "pending_count": pending.count(),
            "items": [{"transfer_id":row.shipment.transfer_id, "shipment":row.shipment.number, "item":row.line.item.name,
                       "quantity":str(row.remaining), "value":str(row.remaining_value)}
                      for row in rows.select_related("shipment", "line__item").order_by("-pk")[:100]]}
