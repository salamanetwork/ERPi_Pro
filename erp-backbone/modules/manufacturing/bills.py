from decimal import Decimal

from django.db.models import Max

from backbone.http import Problem, identifier
from backbone.validation import field_path
from inventory.models import Conversion, Item, Location
from inventory.stock import BASE, quantity_in_base
from purchasing.money import decimal_value

from .models import Bill


def get(rows, pk):
    row = rows.filter(pk=pk).first()
    if row is None:
        raise Problem("not_found", 404)
    return row


def item(data, field="item_id"):
    return get(Item.objects.filter(active=True, is_deleted=False).select_related("base_unit"), identifier(data.get(field), field))


def place(data, field, site=None):
    locations = Location.objects.filter(
        active=True, is_deleted=False,
        warehouse__active=True, warehouse__is_deleted=False, warehouse__site__active=True,
    ).select_related("warehouse")
    if site is not None:
        locations = locations.filter(warehouse__site_id=site)
    return get(locations, identifier(data.get(field), field))


def material_location(data, site):
    key = identifier(data.get("source_id"), "source_id")
    row = Location.objects.filter(
        pk=key, active=True, is_deleted=False,
        warehouse__site_id=site, warehouse__active=True,
        warehouse__is_deleted=False, warehouse__site__active=True,
    ).first()
    if row is None:
        raise Problem("invalid_source_id", field="source_id", reason="invalid_choice")
    return row


def qty(data, factor=Decimal(1)):
    return quantity_in_base(decimal_value(data.get("quantity"), 19, 9, "quantity"), factor)


def effective_bills(site, day):
    return {row.item_id: row for row in Bill.objects.filter(site_id=site, state="released", effective__lte=day)
            .order_by("item_id", "-effective").distinct("item_id")}


def assert_acyclic(bills):
    active, visited = set(), set()

    def visit(product):
        if product in active:
            raise Problem("mfg_bom_cycle", 409)
        if product in visited or product not in bills:
            return
        active.add(product)
        for part in bills[product].components:
            visit(part["item_id"])
        active.remove(product)
        visited.add(product)

    try:
        for product in bills:
            visit(product)
    except RecursionError:
        raise Problem("mfg_bom_depth", 409) from None


def validate_release(bill):
    released = Bill.objects.filter(site_id=bill.site_id, state="released")
    if released.filter(item_id=bill.item_id, effective=bill.effective).exists():
        raise Problem("mfg_bom_effective", 409)
    days = {bill.effective, *released.filter(effective__gt=bill.effective).values_list("effective", flat=True)}
    for day in sorted(days):
        rows = effective_bills(bill.site_id, day)
        if bill.item_id not in rows or rows[bill.item_id].effective < bill.effective:
            rows[bill.item_id] = bill
        assert_acyclic(rows)


def components(data, product, site):
    entries = data.get("components")
    if not isinstance(entries, list) or not 1 <= len(entries) <= 100 or not all(isinstance(line, dict) for line in entries):
        raise Problem("invalid_lines")
    result, seen = [], set()
    for index, line in enumerate(entries):
        with field_path(f"components.{index}"):
            row = item(line)
            if row.pk == product.pk or row.pk in seen:
                raise Problem("mfg_bom_component", 409)
            seen.add(row.pk)
            unit = identifier(line.get("unit_id"), "unit_id", optional=True) or row.base_unit_id
            factor = Decimal(1)
            if unit != row.base_unit_id:
                rows = Conversion.objects.filter(unit_id=unit, base_unit_id=row.base_unit_id)
                rule = rows.filter(item=row).first() or rows.filter(item__isnull=True).first()
                if rule is None:
                    raise Problem("invalid_unit_id", field="unit_id", reason="invalid_choice")
                factor = rule.factor
            source = material_location(line, site) if line.get("source_id") is not None else None
            result.append({"item_id":row.pk, "source_id":source.pk if source else None, "name":row.name, "unit_id":unit, "quantity":str(qty(line)),
                           "factor":str(factor), "base_quantity":str(qty(line,factor)), "base_unit":row.base_unit.code})
    return result


def next_version(site, item_id):
    return (Bill.objects.filter(site_id=site, item_id=item_id).aggregate(value=Max("version"))["value"] or 0) + 1


def scaled(part, amount, output):
    from decimal import ROUND_CEILING, localcontext

    with localcontext() as ctx:
        ctx.prec = 60
        result = (Decimal(part["base_quantity"]) * amount / output).quantize(BASE, rounding=ROUND_CEILING)
    return quantity_in_base(result, Decimal(1))
