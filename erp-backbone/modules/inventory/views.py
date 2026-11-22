from decimal import Decimal

from django.db import transaction
from django.db.models import OuterRef, Subquery, Sum
from django.utils import timezone

from backbone.validation import field_path
from backbone.access import audit, visible
from backbone.http import Problem, body, endpoint, identifier, once, require, site, string
from backbone.services.documents import archive_rows, reject_archived, scoped_document
from backbone.services.numbering import allocate_number
from purchasing.money import decimal_value
from purchasing.models import PurchaseOrder
from purchasing.orders import register_receipt
from purchasing.views import check_revision

from .models import Conversion, Item, Location, Movement, OrderLine, Position, Receipt, Unit, Valuation, Warehouse
from .setup import resolve
from .stock import BASE, business_date, locked_site, prorate, quantity_in_base, record, rounded


def source_order(user, pk, *, lock=False):
    _, row = scoped_document(user, "po", pk, lock=lock)
    require(user, "inventory.view", row.site_id)
    return row


def ready(order):
    if order.status != PurchaseOrder.Status.ISSUED or not order.snapshot:
        raise Problem("purchase_order_not_issued", 409)
    if order.currency != "AED":
        raise Problem("inventory_aed_required", 409)


def line_progress(order):
    rows = {row.line_id: row for row in OrderLine.objects.filter(order=order)}
    return [{**line,
             "item_id": rows[line["id"]].item_id if line["id"] in rows else None,
             "unit_id": rows[line["id"]].unit_id if line["id"] in rows else None,
             "received": str(rows[line["id"]].received if line["id"] in rows else Decimal("0.000")),
             "remaining": str(Decimal(line["quantity"]) -
                              (rows[line["id"]].received if line["id"] in rows else Decimal(0)))}
            for line in order.lines]


def serialize(row):
    return {"id": row.pk, "number": row.number, "order_id": row.order_id, "order_number": row.order.number,
            "site_id": row.site_id, "warehouse_id": row.warehouse_id, "supplier_reference": row.supplier_reference,
            "status": row.status, "revision": row.revision, "lines": row.lines,
            "confirmed_at": row.confirmed_at.isoformat() if row.confirmed_at else None, "confirmed_by": row.confirmed_by,
            "archived": archive_rows("grn").filter(object_id=row.pk).exists()}


def draft_values(order, data):
    warehouse = resolve(Warehouse, data.get("warehouse_id"), "warehouse", site_id=order.site_id, active=True)
    entries = data.get("lines")
    if not isinstance(entries, list) or not 1 <= len(entries) <= 100:
        raise Problem("invalid_lines")
    allowed = {line["id"]: line for line in order.snapshot["lines"]}
    lines = []
    pairs = set()
    for index, entry in enumerate(entries):
        with field_path(f"lines.{index}"):
            if not isinstance(entry, dict):
                raise Problem("invalid_lines")
            line_id = identifier(entry.get("order_line_id"), "order_line")
            location = resolve(Location, entry.get("location_id"), "location", warehouse=warehouse)
            if line_id not in allowed or (line_id, location.pk) in pairs:
                raise Problem("invalid_order_line")
            pairs.add((line_id, location.pk))
            item = resolve(Item, entry.get("item_id"), "item", active=True)
            unit = resolve(Unit, entry.get("unit_id"), "unit")
            quantity = decimal_value(entry.get("quantity"), 7, 3, "quantity")
            if quantity <= 0:
                raise Problem("invalid_quantity", field="quantity", reason="min_value")
            lines.append({"order_line_id": line_id, "location_id": location.pk, "item_id": item.pk,
                          "unit_id": unit.pk, "quantity": format(quantity, ".3f"),
                          "description": allowed[line_id]["description"]})
    return {"warehouse": warehouse, "supplier_reference": string(data, "supplier_reference", 120, required=False),
            "lines": lines}


@endpoint("GET", module="inventory")
def progress(request, pk):
    order = source_order(request.user, pk)
    lines = line_progress(order)
    state = "received" if all(Decimal(line["remaining"]) == 0 for line in lines) else (
        "partially_received" if any(Decimal(line["received"]) > 0 for line in lines) else "not_received")
    rows = Receipt.objects.filter(order=order).exclude(pk__in=archive_rows("grn").values("object_id"))
    return {"order_id": order.pk, "order_number": order.number, "site_id": order.site_id, "currency": order.currency,
            "order_status": order.status, "receipt_status": state, "lines": lines,
            "receipts": list(rows.order_by("-id").values("id", "number", "status"))}


def receipt_rows(user, filters):
    rows = visible(Receipt.objects.select_related("order").order_by("-id"), user, "inventory.view")
    markers = archive_rows("grn").values("object_id")
    rows = rows.filter(pk__in=markers) if filters.get("archived") == "1" else rows.exclude(pk__in=markers)
    return rows


@endpoint("GET", "POST", module="inventory")
def receipts(request):
    user = request.user
    require(user, "inventory.view")
    if request.method == "GET":
        rows = receipt_rows(request.user, request.GET)
        if "page_size" in request.GET:
            from backbone.services.pagination import operational_page
            return {**operational_page(request,rows,serialize,['number', 'status', 'warehouse__name'])}
        return {"items": [serialize(row) for row in rows]}
    data = body(request)
    order_id = identifier(data.get("order_id"), "order")
    order = source_order(user, order_id)
    require(user, "inventory.receive", order.site_id)

    def create():
        current = source_order(user, order_id, lock=True)
        from backbone.site_admin import require_active_site

        require_active_site(current.site_id, lock=True)
        ready(current)
        row = Receipt.objects.create(number=allocate_number("grn", current.site_id), order=current,
                                     site_id=current.site_id, **draft_values(current, data))
        audit(user, "inventory.receipt.create", f"{row.number} / {current.number} / rev 1")
        return serialize(row)

    return once(request, "inventory.receipt.create", data, create)


@endpoint("GET", "PATCH", module="inventory")
def receipt(request, pk):
    user = request.user
    if request.method == "GET":
        return serialize(scoped_document(user, "grn", pk)[1])
    data = body(request)
    with transaction.atomic():
        _, row = scoped_document(user, "grn", pk, lock=True)
        require(user, "inventory.receive", row.site_id)
        reject_archived("grn", row)
        check_revision(data, row)
        if row.status != "draft":
            raise Problem("receipt_not_draft", 409)
        order = source_order(user, row.order_id, lock=True)
        ready(order)
        for field, value in draft_values(order, data).items():
            setattr(row, field, value)
        row.revision += 1
        row.save(update_fields=["warehouse", "supplier_reference", "lines", "revision"])
        audit(user, "inventory.receipt.edit", f"{row.number} / rev {row.revision}")
        return serialize(row)


def bind_line(order, line, item):
    row = OrderLine.objects.filter(order=order, line_id=line["order_line_id"]).first()
    if row:
        if row.item_id != item.pk or row.unit_id != line["unit_id"]:
            raise Problem("order_line_binding_conflict", 409)
        return row
    factor = Decimal(1)
    if line["unit_id"] != item.base_unit_id:
        conversions = Conversion.objects.filter(unit_id=line["unit_id"], base_unit_id=item.base_unit_id)
        conversion = conversions.filter(item=item).first() or conversions.filter(item__isnull=True).first()
        if conversion is None:
            raise Problem("inventory_conversion_missing", 409)
        factor = conversion.factor
    return OrderLine.objects.create(order=order, line_id=line["order_line_id"], item=item,
                                    unit_id=line["unit_id"], factor=factor)


def receipt_movement(user, settings, row, line, source, order_line):
    quantity = Decimal(line["quantity"])
    before = order_line.received
    after = before + quantity
    if after > Decimal(source["quantity"]):
        raise Problem("inventory_over_receipt", 409)
    price = Decimal(source["unit_price"])
    # Round cumulative purchase quantities so split receipts reconcile to the issued line total.
    value = rounded(after * price) - rounded(before * price)
    base = quantity_in_base(quantity, order_line.factor)
    location = Location.objects.get(pk=line["location_id"], warehouse=row.warehouse)
    movement = record(user, settings, order_line.item, location, "receipt", base, value, {
        "purchase_quantity": str(quantity), "purchase_unit_id": order_line.unit_id,
        "conversion_factor": str(order_line.factor), "po_id": row.order_id, "po_line_id": order_line.line_id,
        "snapshot_version": row.order.snapshot["snapshot_version"], "issued_unit_price": str(price),
        "event_type": "inventory.receipt_confirmed.v1",
    }, receipt=row)
    order_line.received = after
    order_line.save(update_fields=["received"])
    return {**line, "base_quantity": str(base), "amount": str(value), "unit_price": str(price),
            "movement_id": movement.pk, "factor": str(order_line.factor)}


@endpoint("POST", module="inventory")
def confirm(request, pk):
    user = request.user
    data = body(request)
    _, receipt_row = scoped_document(user, "grn", pk)
    source_order(user, receipt_row.order_id)
    require(user, "inventory.confirm", receipt_row.site_id)

    def commit():
        _, row = scoped_document(user, "grn", pk, lock=True)
        reject_archived("grn", row)
        check_revision(data, row)
        if row.status != "draft":
            raise Problem("receipt_not_draft", 409)
        # Same PO lock as Void, then the site lock used by opening, receipts, and reversals.
        order = PurchaseOrder.objects.select_for_update().get(pk=row.order_id)
        ready(order)
        register_receipt(order.pk, f"grn:{row.pk}")
        settings = locked_site(row.site_id)
        if not Warehouse.objects.filter(pk=row.warehouse_id, active=True).exists():
            raise Problem("invalid_warehouse", 409)
        sources = {line["id"]: line for line in order.snapshot["lines"]}
        confirmed = []
        for line in row.lines:
            item = resolve(Item, line["item_id"], "item", active=True)
            binding = bind_line(order, line, item)
            confirmed.append(receipt_movement(user, settings, row, line, sources[binding.line_id], binding))
        row.lines, row.status, row.confirmed_at = confirmed, "confirmed", timezone.now()
        row.confirmed_by = user.get_username()
        row.revision += 1
        row.save(update_fields=["lines", "status", "confirmed_at", "confirmed_by", "revision"])
        if settings.opening_closed_at is None:
            settings.opening_closed_at = row.confirmed_at
            settings.save(update_fields=["opening_closed_at"])
            audit(user, "inventory.opening.close", f"Site #{row.site_id} / {row.number}")
        audit(user, "inventory.receipt.confirm", f"{row.number} / {order.number} / rev {row.revision}")
        return serialize(row)

    return once(request, "inventory.receipt.confirm", {**data, "id": pk}, commit)


def movement_record(row):
    return {"id": row.pk, "site_id": row.site_id, "item_id": row.item_id, "location_id": row.location_id,
            "warehouse_id": row.location.warehouse_id, "kind": row.kind, "quantity": str(row.quantity),
            "value": str(row.value), "receipt_id": row.receipt_id, "original_id": row.original_id,
            "actor": row.actor, "occurred_at": row.occurred_at.isoformat(),
            "business_date": row.business_date.isoformat(), "facts": row.facts}


@endpoint("GET", module="inventory")
def movements(request):
    require(request.user, "inventory.view")
    rows = visible(Movement.objects.select_related("location").order_by("-id"), request.user, "inventory.view")
    before = request.GET.get("before")
    if before:
        if not before.isascii() or not before.isdecimal() or len(before) > 19 or int(before) >= 2**63:
            raise Problem("invalid_cursor")
        rows = rows.filter(pk__lt=int(before))
    if "page_size" in request.GET:
        from backbone.services.pagination import operational_page
        return {**operational_page(request,rows,movement_record,['kind', 'item__name', 'location__name'])}
    page = list(rows[:101])
    return {"items": [movement_record(row) for row in page[:100]],
            "next_before": page[99].pk if len(page) > 100 else None}


@endpoint("GET", module="inventory")
def balances(request):
    require(request.user, "inventory.view")
    positions = visible(Position.objects.filter(quantity__gt=0).select_related("location").order_by("site_id", "item_id", "location_id"),
                        request.user, "inventory.view")
    site_values = Valuation.objects.filter(site_id=OuterRef("site_id"), item_id=OuterRef("item_id"))
    from django.db.models.functions import Coalesce
    from supply.models import Reservation

    reserved = Reservation.objects.filter(location_id=OuterRef("location_id"), item_id=OuterRef("item_id"),
                                          quantity__gt=0).values("location_id").annotate(total=Sum("quantity"))
    positions = positions.annotate(reserved=Coalesce(Subquery(reserved.values("total")), Decimal(0)))
    positions = positions.annotate(site_value=Subquery(site_values.values("value")),
                                   site_quantity=Subquery(site_values.values("quantity")))
    def position(row):
        return {"site_id": row.site_id, "item_id": row.item_id, "location_id": row.location_id,
                       "warehouse_id": row.location.warehouse_id, "quantity": str(row.quantity),
                       "on_hand": str(row.quantity), "reserved": str(row.reserved), "available": str(row.quantity - row.reserved),
                       "site_average": str(prorate(row.site_value, Decimal(1), row.site_quantity, BASE))}
    if "page_size" in request.GET:
        from backbone.services.pagination import operational_page
        return operational_page(request,positions,position,["item__name","location__name","site__name"])
    return {"items":[position(row) for row in positions]}


@endpoint("POST", module="inventory")
def reverse(request, pk):
    user = request.user
    data = body(request)
    original = visible(Movement.objects.filter(kind="receipt"), user, "inventory.view").filter(pk=pk).first()
    if original is None:
        raise Problem("not_found", 404)
    kind = data.get("kind")
    if kind not in ("correction", "return"):
        raise Problem("invalid_movement_kind")
    require(user, "inventory.correct" if kind == "correction" else "inventory.return", original.site_id)
    reason = string(data, "reason", 1000)
    quantity = decimal_value(data.get("quantity"), 7, 3, "quantity")
    if quantity <= 0:
        raise Problem("invalid_quantity", field="quantity", reason="min_value")

    def commit():
        order = PurchaseOrder.objects.select_for_update().get(pk=original.facts["po_id"])
        settings = locked_site(original.site_id)
        binding = OrderLine.objects.get(order=order, line_id=original.facts["po_line_id"])
        factor = Decimal(original.facts["conversion_factor"])
        base = quantity_in_base(quantity, factor)
        used = -(original.reversals.aggregate(total=Sum("quantity"))["total"] or Decimal(0))
        if base + used > original.quantity or quantity > binding.received:
            raise Problem("inventory_return_exceeds_receipt", 409)
        original_cost = prorate(original.value, used + base, original.quantity) - prorate(original.value, used, original.quantity)
        from accounting.payables import return_split
        from accounting.services import ledger_lock

        ledger_lock()
        original_cost, billed = return_split(original, quantity, original_cost, kind, business_date(settings, timezone.now()))
        row = record(user, settings, original.item, original.location, kind, -base, -original_cost, {
            **original.facts, "reason": reason, "purchase_quantity": str(quantity),
            "original_receipt_value": str(original_cost), "ap_return_parts": billed,
            "event_type": "inventory.receipt_reversed.v1",
        }, receipt=original.receipt, original=original)
        binding.received -= quantity
        binding.save(update_fields=["received"])
        audit(user, f"inventory.{kind}", f"{original.receipt.number} / {order.number} / movement #{row.pk}")
        return movement_record(row)

    return once(request, "inventory.receipt.reverse", {**data, "original_id": pk}, commit)


@endpoint("POST", module="inventory")
def opening(request):
    user = request.user
    data = body(request)
    site_id = site(data)
    if site_id is None:
        raise Problem("invalid_site")
    require(user, "inventory.view", site_id)
    require(user, "inventory.opening", site_id)

    def commit():
        settings = locked_site(site_id)
        if settings.opening_closed_at:
            raise Problem("inventory_opening_closed", 409)
        lines = data.get("lines")
        close = data.get("close", False)
        if type(close) is not bool or not isinstance(lines, list) or len(lines) > 100 or (not lines and not close):
            raise Problem("invalid_opening_lines")
        results = []
        for index, line in enumerate(lines):
            with field_path(f"lines.{index}"):
                if not isinstance(line, dict):
                    raise Problem("invalid_opening_lines")
                item = resolve(Item, line.get("item_id"), "item", active=True)
                location = resolve(Location, line.get("location_id"), "location", warehouse__site_id=site_id, warehouse__active=True)
                quantity = decimal_value(line.get("quantity"), 9, 9, "quantity")
                cost = decimal_value(line.get("unit_cost"), 9, 9, "unit_cost")
                if quantity <= 0:
                    raise Problem("invalid_quantity", field="quantity", reason="min_value")
                if Movement.objects.filter(site_id=site_id, item=item, location=location, kind="opening").exists():
                    raise Problem("inventory_opening_duplicate", 409)
                row = record(user, settings, item, location, "opening", quantity, rounded(quantity * cost),
                             {"input_unit_cost": str(cost), "event_type": "inventory.opening_recorded.v1"})
                results.append(movement_record(row))
                audit(user, "inventory.opening.record", f"Site #{site_id} / movement #{row.pk}")
        if close:
            settings.opening_closed_at = timezone.now()
            settings.save(update_fields=["opening_closed_at"])
            audit(user, "inventory.opening.close", f"Site #{site_id}")
        return {"items": results, "closed": close}

    return once(request, "inventory.opening", data, commit)
