"""Read-side planning contracts for Inventory."""
from decimal import Decimal

from django.db.models import Sum

from backbone.http import Problem
from purchasing.models import PurchaseOrder
from .models import Conversion, OrderLine, Position


def on_hand(site_id):
    return dict(Position.objects.filter(site_id=site_id).values("item_id").annotate(
        total=Sum("quantity")).values_list("item_id","total"))


def incoming_purchase_lines(site_id, mappings):
    orders = PurchaseOrder.objects.filter(site_id=site_id,status="issued",currency="AED")
    bindings = {(row.order_id,row.line_id):row for row in OrderLine.objects.filter(order__in=orders)}
    for order in orders.iterator():
        for line in order.lines:
            key = (order.pk,line["id"])
            bound = bindings.get(key)
            mapping = mappings.get(key)
            if bound:
                remaining = (Decimal(line["quantity"])-bound.received)*bound.factor
                if remaining>0:
                    yield bound.item_id,order.pk,remaining
            elif mapping:
                item, unit_id = mapping
                factor = Decimal(1)
                if unit_id and unit_id!=item.base_unit_id:
                    rules = Conversion.objects.filter(unit_id=unit_id,base_unit_id=item.base_unit_id)
                    rule = rules.filter(item=item).first() or rules.filter(item__isnull=True).first()
                    factor = rule.factor if rule else Decimal(0)
                if factor>0:
                    yield item.pk,order.pk,Decimal(line["quantity"])*factor
                else:
                    raise Problem("mfg_purchase_unit",409)
            else:
                yield None,order.pk,Decimal(0)
