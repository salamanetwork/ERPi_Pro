from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP, localcontext
from zoneinfo import ZoneInfo

from django.utils import timezone

from backbone.http import Problem
from .models import Movement, Position, SiteSettings, Valuation

CENT = Decimal("0.01")
BASE = Decimal("0.000000001")


def rounded(value, scale=CENT):
    return value.quantize(scale, rounding=ROUND_HALF_UP)


def prorate(value, numerator, denominator, scale=CENT):
    with localcontext() as ctx:
        ctx.prec = 60
        return rounded(value * numerator / denominator, scale)


def quantity_in_base(quantity, factor):
    value = quantity * factor
    if value <= 0 or value != rounded(value, BASE) or value >= 10**19:
        raise Problem("inventory_quantity_precision")
    return value


def locked_site(site_id):
    # Serialize stock updates and the opening cutover within each site.
    from backbone.site_admin import require_active_site

    require_active_site(site_id, lock=True)
    settings, _ = SiteSettings.objects.get_or_create(site_id=site_id)
    return settings


def balance_rows(site_id, item, location):
    position, _ = Position.objects.get_or_create(site_id=site_id, item=item, location=location)
    valuation, _ = Valuation.objects.get_or_create(site_id=site_id, item=item)
    return position, valuation


def business_date(settings, moment):
    if settings is None:
        return moment.date()
    local = moment.astimezone(ZoneInfo(settings.timezone))
    return local.date() - timedelta(days=local.time() < settings.day_start)


def record(user, settings, item, location, kind, quantity, value, facts, *, receipt=None, original=None, hold_owner=None, moving_average=False):
    from .models import Location

    if getattr(item, "is_deleted", False):
        raise Problem("invalid_item", 409)
    if not Location.objects.filter(
        pk=location.pk,
        active=True,
        is_deleted=False,
        warehouse__active=True,
        warehouse__is_deleted=False,
        warehouse__site_id=settings.site_id,
    ).exists():
        raise Problem("invalid_location", 409)
    position, valuation = balance_rows(settings.site_id, item, location)
    from django.db import connection
    if connection.in_atomic_block:
        position = Position.objects.select_for_update().get(pk=position.pk)
        valuation = Valuation.objects.select_for_update().get(pk=valuation.pk)
    if quantity < 0:
        from supply.reservations import withdraw

        withdraw(position, -quantity, transfer_line=facts.get("transfer_line_id") if kind == "transfer_out" else None,
                 order=facts.get("sales_order_id") if kind == "sale" else None, order_line=facts.get("order_line_id"), owner_key=hold_owner)
    if moving_average or kind in ("return", "sale", "maint_issue", "transfer_out"):
        value = -prorate(valuation.value, -quantity, valuation.quantity) if valuation.quantity else Decimal(0)
    position.quantity += quantity
    valuation.quantity += quantity
    valuation.value += value
    if position.quantity < 0 or valuation.quantity < 0:
        raise Problem("insufficient_stock", 409)
    if valuation.value < 0 or (valuation.quantity == 0 and valuation.value != 0):
        raise Problem("inventory_correction_value_conflict", 409)
    if max(position.quantity, valuation.quantity) >= 10**19 or valuation.value >= 10**24:
        raise Problem("inventory_balance_limit", 409)
    position.save(update_fields=["quantity"])
    valuation.save(update_fields=["quantity", "value"])
    now = timezone.now()
    date = business_date(settings, now)
    if kind in ("transfer_in", "transfer_back"):
        date = max(date, original.business_date)
    from accounting.services import ledger_lock, observe

    ledger = ledger_lock()
    facts = {**facts, "currency": ledger.currency, "cost_policy": "moving_average", "cost_policy_version": 1,
             "base_unit_id": item.base_unit_id, "site_timezone": settings.timezone,
             "day_start": settings.day_start.isoformat(), "movement_unit_cost": str(prorate(value, Decimal(1), quantity, BASE) if quantity else Decimal(0))}
    movement = Movement.objects.create(site_id=settings.site_id, item=item, location=location, kind=kind,
        quantity=quantity, value=value, receipt=receipt, original=original, actor=user.get_username(),
        actor_id=user.pk, occurred_at=now, business_date=date, facts=facts)
    if kind not in ("sale", "sale_return"):
        observe(ledger, movement)
    return movement
