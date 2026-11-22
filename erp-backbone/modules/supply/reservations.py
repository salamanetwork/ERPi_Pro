"""All mutations execute under Inventory's site lock."""
from decimal import Decimal

from django.db.models import Sum

from backbone.http import Problem
from inventory.models import Position
from .models import Reservation

ZERO = Decimal("0")


def total(rows):
    return rows.aggregate(total=Sum("quantity"))["total"] or ZERO


def reserve(site, item, location, quantity, **owner):
    capacity = available(site, item, location)
    if quantity > capacity:
        raise Problem("supply_insufficient_available", 409)
    row, _ = Reservation.objects.get_or_create(site_id=site, item_id=item, location_id=location,
                                                defaults={"quantity": ZERO}, **owner)
    row.quantity += quantity
    row.save(update_fields=["quantity"])
    return row


def available(site, item, location):
    position = Position.objects.filter(site_id=site, item_id=item, location_id=location).first()
    rows = Reservation.objects.filter(site_id=site, item_id=item, location_id=location, quantity__gt=0)
    return (position.quantity if position else ZERO) - total(rows)


def held(owner_key):
    return total(Reservation.objects.filter(owner_key=owner_key))


def release_owner(owner_key):
    Reservation.objects.filter(owner_key=owner_key, quantity__gt=0).update(quantity=0)


def planning_holds(site):
    return Reservation.objects.filter(site_id=site, quantity__gt=0).values("item_id", "quantity", "order_id", "owner_key")


def withdraw(position, quantity, *, transfer_line=None, order=None, order_line=None, owner_key=None):
    rows = Reservation.objects.filter(item_id=position.item_id, location_id=position.location_id, quantity__gt=0)
    own = rows.none()
    if transfer_line is not None:
        own = rows.filter(transfer_line_id=transfer_line)
    elif order is not None:
        own = rows.filter(order_id=order, order_line=order_line)
    elif owner_key is not None:
        own = rows.filter(owner_key=owner_key)
    shared, reserved = total(rows), total(own)
    if quantity > position.quantity - shared + reserved:
        raise Problem("insufficient_stock" if not shared else "supply_insufficient_available", 409)
    if reserved:
        row = own.get()
        row.quantity -= min(quantity, row.quantity)
        row.save(update_fields=["quantity"])


def release_excess(order, order_line, remaining):
    rows = Reservation.objects.filter(order=order, order_line=order_line, quantity__gt=0)
    excess = total(rows) - remaining
    if excess <= ZERO:
        return
    for row in rows.order_by("-pk"):
        released = min(excess, row.quantity)
        row.quantity -= released
        row.save(update_fields=["quantity"])
        excess -= released
        if not excess:
            break
