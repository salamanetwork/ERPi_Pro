from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from backbone.http import Problem
from backbone.registry import enabled_modules
from inventory.planning import incoming_purchase_lines, on_hand
from sales.planning import open_requirements
from supply.planning import incoming_shipments
from supply.reservations import planning_holds

from .bills import assert_acyclic, effective_bills, qty, scaled
from .models import Order, Policy, Proposal
from .production import owner, usable


def horizon(data, today):
    from accounting.views import day_field

    start = day_field(data, "start") if data.get("start") else today
    if start < today or start.year > 9998:
        raise Problem("invalid_start", field="start")
    end = day_field(data, "end") if data.get("end") else start+timedelta(days=29)
    if (end-start).days not in range(365):
        raise Problem("invalid_end", field="end")
    return start, end


def calculate(plan, requirements, po_dates, mappings, *, persist=True):
    active = enabled_modules()
    site, start, end = plan.site_id, plan.start, plan.end
    stock = on_hand(site)
    demand, supply = defaultdict(list), defaultdict(list)
    held = defaultdict(Decimal)
    for row in planning_holds(site):
        ident = row["owner_key"] or (f"sales:{row['order_id']}" if row["order_id"] else "")
        held[ident,row["item_id"]] += row["quantity"]
        stock[row["item_id"]] = stock.get(row["item_id"], Decimal(0)) - row["quantity"]

    def need(item, date, quantity, ref, reservation=""):
        day = max(start, date)
        if reservation and day <= end:
            used = min(quantity, held[reservation,item])
            quantity -= used
            held[reservation,item] -= used
        if quantity > 0 and day <= end:
            demand[item].append((day, quantity, ref))

    def expect(item, date, quantity):
        if quantity > 0 and date <= end:
            supply[item].append((max(start,date), quantity))

    excluded = {"purchase_lines":0,"unmapped_purchase_lines":0,"shipments":0}
    if "sales" in active:
        for item, day, quantity, number, reservation in open_requirements(site):
            need(item, day, quantity, number, reservation)
    for row in Order.objects.filter(site_id=site, state="released").prefetch_related("materials").iterator(chunk_size=100):
        expect(row.item_id,row.due,row.quantity)
        for material in row.materials.all():
            need(material.item_id, row.due, max(Decimal(0),material.required-usable(material)),row.number,owner(material))
    if "purchase_orders" in active:
        for item, order_id, quantity in incoming_purchase_lines(site,mappings):
            expected = po_dates.get(str(order_id))
            if item is None:
                excluded["unmapped_purchase_lines"] += 1
            elif expected:
                expect(item,expected,quantity)
            else:
                excluded["purchase_lines"] += 1
    if "supply" in active:
        for item, expected, amount in incoming_shipments(site):
            if expected:
                expect(item,expected,amount)
            else:
                excluded["shipments"] += 1
    for req in requirements:
        need(req["item_id"], req["date"], qty(req), "manual")
    bills = effective_bills(site,start)
    assert_acyclic(bills)
    policies = {row.item_id:row for row in Policy.objects.filter(site_id=site)}
    seen, order = set(), []

    def visit(product):
        if product in seen:
            return
        seen.add(product)
        policy = policies.get(product)
        if product in bills and (not policy or policy.method=="make"):
            for part in bills[product].components:
                visit(part["item_id"])
        order.append(product)

    for product in sorted(set(demand)|set(bills)):
        visit(product)
    proposals = []
    for product in reversed(order):
        inbound = sorted(supply[product], reverse=True)
        remaining = stock.get(product,Decimal(0))
        policy = policies.get(product)
        bill = bills.get(product)
        method = policy.method if policy else ("make" if bill else "buy")
        for day, amount, ref in sorted(demand[product]):
            while inbound and inbound[-1][0] <= day:
                remaining += inbound.pop()[1]
            uncovered = max(Decimal(0), amount-remaining)
            remaining = max(Decimal(0), remaining-amount)
            if not uncovered:
                continue
            latest = day-timedelta(days=policy.lead_days if policy else 0)
            facts = {"demand":ref,"bill_id":bill.pk if method=="make" and bill else None,
                     "next_supply":inbound[-1][0].isoformat() if inbound else None,
                     "source_id":policy.source_id if policy else None,"destination_id":policy.destination_id if policy else None,
                     "late":latest<start}
            proposals.append(Proposal(plan=plan,item_id=product,quantity=uncovered,due=day,start=latest,
                                      method=method,facts=facts))
            if method=="make" and bill:
                for part in bill.components:
                    need(part["item_id"],latest,scaled(part,uncovered,bill.quantity),f"make:{product}:{day}")
    if not persist:
        return proposals
    Proposal.objects.bulk_create(proposals)
    return excluded
