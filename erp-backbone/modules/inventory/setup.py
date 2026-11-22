import re
from datetime import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import transaction
from django.db.models import F

from backbone.access import audit, visible
from backbone.http import Problem, body, endpoint, identifier, require, site, string
from backbone.models import Site
from purchasing.money import decimal_value

from .models import Conversion, Item, Location, SiteSettings, Unit, Warehouse
from .stock import locked_site


def resolve(model, value, field, **filters):
    row = model.objects.filter(pk=identifier(value, field), **filters).first()
    if row is None:
        raise Problem(f"invalid_{field}")
    return row


def coded(model, data, user, action, **scope):
    code = string(data, "code", 24 if model is Unit else 32).upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]*", code):
        raise Problem("invalid_code")
    row, created = model.objects.get_or_create(code=code, **scope, defaults={
        "name": string(data, "name", 80 if model is Unit else 120),
        **({"base_unit": resolve(Unit, data.get("base_unit_id"), "base_unit")} if model is Item else {}),
    })
    if not created:
        raise Problem("inventory_code_exists", 409)
    audit(user, f"inventory.{action}", code)
    return {"id": row.pk, "code": row.code}, 201


@endpoint("GET", module="inventory")
def catalog(request):
    user = request.user
    require(user, "inventory.view")
    warehouses = visible(Warehouse.objects.order_by("code"), user, "inventory.view")
    settings = visible(SiteSettings.objects.all(), user, "inventory.view")
    return {
        "units": list(Unit.objects.order_by("code").values()),
        "items": list(Item.objects.order_by("code").values()),
        "conversions": [{"id": row.pk, "item_id": row.item_id, "unit_id": row.unit_id,
                         "base_unit_id": row.base_unit_id, "factor": str(row.factor)}
                        for row in Conversion.objects.order_by("id")],
        "warehouses": list(warehouses.values()),
        "locations": list(Location.objects.filter(warehouse__in=warehouses).order_by("code").values()),
        "sites": list(visible_sites(user)),
        "settings": [{"site_id": row.site_id, "timezone": row.timezone, "day_start": row.day_start.isoformat(),
                      "opening_closed_at": row.opening_closed_at.isoformat() if row.opening_closed_at else None}
                     for row in settings],
    }


def visible_sites(user):
    from backbone.access import scopes

    allowed = scopes(user, "inventory.view")
    sites = Site.objects.filter(active=True).order_by("name")
    return (sites if None in allowed else sites.filter(pk__in=allowed)).values("id", "name")


@endpoint("POST", module="inventory")
def configure(request, kind):
    data = body(request)
    user = request.user
    require(user, "inventory.view")
    with transaction.atomic():
        if kind in ("unit", "item", "conversion", "item-status"):
            require(user, "inventory.setup", None)
            if kind == "unit":
                return coded(Unit, data, user, kind)
            if kind == "item":
                return coded(Item, data, user, kind)
            if kind == "item-status":
                item = resolve(Item, data.get("item_id"), "item")
                if type(data.get("active")) is not bool:
                    raise Problem("invalid_active")
                Item.objects.filter(pk=item.pk).update(active=data["active"], revision=F("revision") + 1)
                audit(user, "inventory.item-status", item.code)
                return {"id": item.pk, "active": data["active"]}
            item = resolve(Item, data["item_id"], "item") if data.get("item_id") is not None else None
            unit = resolve(Unit, data.get("unit_id"), "unit")
            base = item.base_unit if item else resolve(Unit, data.get("base_unit_id"), "base_unit")
            factor = decimal_value(data.get("factor"), 9, 9, "factor")
            if unit == base or factor <= 0:
                raise Problem("invalid_factor")
            row, _ = Conversion.objects.update_or_create(item=item, unit=unit, base_unit=base,
                                                          defaults={"factor": factor})
            audit(user, "inventory.conversion", f"#{row.pk}")
            return {"id": row.pk, "factor": str(factor)}
        if kind == "location":
            from backbone.site_admin import require_active_site

            warehouse = resolve(Warehouse, data.get("warehouse_id"), "warehouse", active=True)
            require_active_site(warehouse.site_id, lock=True)
            warehouse = resolve(Warehouse, warehouse.pk, "warehouse", active=True)
            require(user, "inventory.view", warehouse.site_id)
            require(user, "inventory.setup", warehouse.site_id)
            return coded(Location, data, user, kind, warehouse=warehouse)
        site_id = site(data)
        if site_id is None:
            raise Problem("invalid_site")
        require(user, "inventory.view", site_id)
        require(user, "inventory.setup", site_id)
        if kind == "warehouse":
            return coded(Warehouse, data, user, kind, site_id=site_id)
        if kind != "site":
            raise Problem("not_found", 404)
        zone = string(data, "timezone", 80)
        start = string(data, "day_start", 5)
        try:
            ZoneInfo(zone)
            if not re.fullmatch(r"[0-9]{2}:[0-9]{2}", start):
                raise ValueError
            day_start = time.fromisoformat(start)
        except (ZoneInfoNotFoundError, ValueError):
            raise Problem("invalid_business_day") from None
        row = locked_site(site_id)
        row.timezone, row.day_start = zone, day_start
        row.save(update_fields=["timezone", "day_start"])
        audit(user, "inventory.site", f"#{site_id}")
        return {"site_id": site_id, "timezone": zone, "day_start": start}


@endpoint("PATCH", module="inventory")
def warehouse_state(request, pk):
    from backbone.site_admin import require_active_site
    from .models import Position, Receipt
    from backbone.services.documents import archive_rows

    active = body(request).get("active")
    if type(active) is not bool:
        raise Problem("invalid_warehouse")
    with transaction.atomic():
        row = resolve(Warehouse, pk, "warehouse")
        require(request.user, "inventory.setup", row.site_id)
        require(request.user, "inventory.view", row.site_id)
        require_active_site(row.site_id, lock=True)
        row = Warehouse.objects.select_for_update().get(pk=pk)
        if not active and (Position.objects.filter(location__warehouse=row).exclude(quantity=0).exists()
                or Receipt.objects.filter(warehouse=row, status="draft")
                    .exclude(pk__in=archive_rows("grn").values("object_id")).exists()):
            raise Problem("warehouse_has_open_records", 409)
        if row.active != active:
            row.active = active
            row.save(update_fields=["active"])
            audit(request.user, "warehouse.activate" if active else "warehouse.deactivate", row.pk)
        return {"id": row.pk, "active": row.active}


def item_directory(params):
    from backbone.services.pagination import directory_rows, positive_filter

    rows = directory_rows(Item.objects.select_related("base_unit").order_by("code"), params)
    unit = positive_filter(params, "unit")
    return rows.filter(base_unit_id=unit) if unit else rows


def item_record(row):
    return {"id": row.pk, "code": row.code, "name": row.name, "active": row.active,
            "base_unit_id": row.base_unit_id, "unit": row.base_unit.code, "revision": row.revision}


@endpoint("GET", "POST", module="inventory")
def items(request):
    from backbone.services.pagination import paginate

    require(request.user, "inventory.view")
    if request.method == "GET":
        return {**paginate(request, item_directory(request.GET), item_record),
                "units": list(Unit.objects.order_by("code").values("id", "code", "name"))}
    return configure(request, "item")


@endpoint("GET", "PATCH", module="inventory")
def item(request, pk):
    require(request.user, "inventory.view")
    row = resolve(Item, pk, "item")
    if request.method == "GET":
        return {"record": item_record(row), "units": list(Unit.objects.order_by("code").values("id", "code", "name")),
                "conversions": [{"id": entry.pk, "unit_id": entry.unit_id, "unit": entry.unit.code,
                                 "factor": str(entry.factor)} for entry in
                                Conversion.objects.filter(item=row).select_related("unit").order_by("unit__code")]}
    require(request.user, "inventory.setup", None)
    data = body(request)
    if type(data.get("active")) is not bool:
        raise Problem("invalid_active")
    name = string(data, "name", 120)
    with transaction.atomic():
        row = Item.objects.select_for_update().get(pk=pk)
        if identifier(data.get("revision"), "revision") != row.revision:
            raise Problem("revision_conflict", 409)
        if data.get("code", row.code) != row.code or data.get("base_unit_id", row.base_unit_id) != row.base_unit_id:
            raise Problem("inventory_item_identity_locked", 409)
        row.name, row.active = name, data["active"]
        row.revision += 1
        row.save(update_fields=["name", "active", "revision"])
        audit(request.user, "inventory.item.update", row.code)
    return item_record(row)
