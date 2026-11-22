from collections import defaultdict
from decimal import Decimal

from django.db.models import Sum

from accounting.models import InvoiceMatch, Line, SettlementAllocation
from inventory.models import Movement
from .models import ExpenseLink, PurchaseLink, TimeAllocation, WorkLink

ZERO = Decimal(0)


def money(value):
    return format(value,".2f")


def purchase_costs(project):
    links = list(PurchaseLink.objects.filter(task__project=project).select_related("order","task"))
    if not links:
        return []
    active = InvoiceMatch.objects.filter(invoice__order_id__in={link.order_id for link in links},
                                         invoice__status="posted",invoice__entry__posted_at__isnull=False)
    credits = dict(SettlementAllocation.objects.filter(match__in=active,settlement__kind="credit",
                settlement__entry__posted_at__isnull=False).values("match_id").annotate(amount=Sum("amount")).values_list("match_id","amount"))
    settled = defaultdict(Decimal)
    for match in active.select_related("invoice","accrual__receipt_movement"):
        facts = match.accrual.receipt_movement.facts
        key = (match.invoice.order_id,facts.get("po_line_id"))
        settled[key] += match.amount-credits.get(match.pk,ZERO)
    output = []
    for link in links:
        order = link.order
        line = next((line for line in (order.snapshot or {}).get("lines",order.lines) if line["id"]==link.line_id),None)
        if not line:
            continue
        ordered = Decimal(line["amount"])
        billed = settled[(order.pk,link.line_id)]
        committed = max(ZERO,ordered-billed) if order.status=="issued" else ZERO
        output.append({"source_id":order.pk,"reference":order.number,"line_id":link.line_id,"task":link.task.title,
                       "state":order.status,"ordered":money(ordered),"billed_stock":money(billed),"committed":money(committed)})
    return output


def actual_costs(project):
    rows = []
    work = {link.order_id:link for link in WorkLink.objects.filter(task__project=project).select_related("task")}
    moves = Movement.objects.filter(kind__in=("maint_issue","maint_return"),facts__work_order_id__in=list(work),
                                    posting__entry__posted_at__isnull=False).select_related("posting__entry")
    for move in moves:
        rows.append({"type":"materials","task":work[move.facts["work_order_id"]].task.title,
                     "entry_id":move.posting.entry_id,"reference":move.posting.entry.number,
                     "amount":money(-move.value),"order_id":move.facts["work_order_id"]})
    links = ExpenseLink.objects.filter(task__project=project).select_related("task","line__entry__reversed_by","line__account")
    for link in links:
        line = link.line
        rows.append({"type":"expense","task":link.task.title,"entry_id":line.entry_id,
                     "reference":line.entry.number,"amount":money(line.amount)})
        reversal = getattr(line.entry,"reversed_by",None)
        if reversal and reversal.posted_at:
            rows.append({"type":"expense","task":link.task.title,"entry_id":reversal.pk,
                         "reference":reversal.number,"amount":money(-line.amount)})
    return rows


def report(project):
    baseline = project.baselines.order_by("-version").first()
    groups = {key:Decimal(amount) for key,amount in (baseline.categories if baseline else {}).items()}
    purchases = purchase_costs(project)
    actual = actual_costs(project)
    time = TimeAllocation.objects.filter(task__project=project,sheet__state="approved").select_related("task")
    labour = [{"task":row.task.title,"sheet_id":row.sheet_id,"timer_id":row.timer_id,
               "seconds":row.approved_seconds,"amount":money(row.approved_cost)} for row in time if row.approved_cost is not None]
    posted = defaultdict(Decimal)
    for row in actual:
        posted[row["type"]] += Decimal(row["amount"])
    posted["labour"] = sum((Decimal(row["amount"]) for row in labour),ZERO)
    committed = sum((Decimal(row["committed"]) for row in purchases),ZERO)
    planned = sum(groups.values(),ZERO)
    total_posted = posted["materials"]+posted["expense"]
    combined = total_posted+posted["labour"]
    return {"baseline":baseline.version if baseline else None, "currency":"AED", "planned":money(planned),
            "planned_hours":str(baseline.hours) if baseline else "0.00",
            "actual_hours":money(Decimal(sum(row["seconds"] for row in labour))/3600),
            "committed":money(committed),"gl_actual":money(total_posted),"labour":money(posted["labour"]),
            "actual":money(combined),"variance":money(planned-combined),"available":money(planned-combined-committed),
            "over_budget":combined+committed>planned,
            "categories":[{"category":key,"planned":money(groups.get(key,ZERO)),"actual":money(posted[key])}
                          for key in ("materials","expense","labour")],
            "purchases":purchases,"entries":actual,"time":labour}
