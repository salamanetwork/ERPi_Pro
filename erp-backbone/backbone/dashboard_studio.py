import re
from django.db.models import Q, Sum
from django.db import transaction
from .http import endpoint, body, Problem
from .models import DashboardPreference
from .access import has_permission, visible
from .registry import enabled_modules
from .services import reports, workspace_charts
from django.utils import timezone

PRESETS = {"general", "finance", "sales", "procurement", "operations"}
def validate(layout):
    def invalid():
        raise Problem("invalid_layout", field="layout")
    if not isinstance(layout, dict) or not isinstance(layout.get("boards"), dict):
        invalid()
    boards = layout["boards"]
    if len(boards) > 50 or not isinstance(layout.get("default"),str) or layout.get("default") not in [*PRESETS, *boards]:
        invalid()
    for key, board in boards.items():
        if not (key in PRESETS or re.fullmatch(r"custom-[a-zA-Z0-9-]{1,55}",key)) or not isinstance(board, dict):
            invalid()
        if any(not isinstance(board.get(k), str) or len(board[k]) > 100 for k in ("en", "ar")):
            invalid()
        widgets = board.get("widgets")
        if not isinstance(widgets, list) or len(widgets) > 200:
            invalid()
        seen = set()
        for w in widgets:
            if not isinstance(w, dict) or not isinstance(w.get("id"), str) or not re.fullmatch(r"[\w:-]{1,100}",w["id"]) or w["id"] in seen or w.get("size") not in ("compact","medium","full"):
                invalid()
            seen.add(w["id"])
    return layout

@endpoint("GET", "PATCH")
def preferences(request):
    if request.method == "GET":
        return DashboardPreference.objects.filter(user=request.user).values("layout","revision").first() or {"revision":0,"layout":{}}
    data = body(request)
    layout = validate(data.get("layout"))
    with transaction.atomic():
        row,_ = DashboardPreference.objects.select_for_update().get_or_create(user=request.user)
        if type(data.get("revision")) is not int or data["revision"] != row.revision:
            raise Problem("revision_conflict",409)
        row.layout = layout
        row.revision += 1
        row.save()
        return {"layout":layout,"revision":row.revision}

@endpoint("GET")
def blocks(request):
    selected = workspace_charts.filters_for_dashboard(request.GET,timezone.localdate())
    user = request.user
    enabled = enabled_modules()
    site = int(selected["site"]) if selected.get("site") else None
    result = {}
    metrics = ("pending-postings","open-purchases","overdue-purchases","stock-value","unbilled-sales","pm-due","maintenance-open")
    for metric in metrics:
        if workspace_charts.eligible(user,metric,enabled,selected):
            filters = reports.effective(metric,selected)
            row = reports.summarize(user,metric,filters,page=1,size=5)
            result[metric] = row
    def scoped(rows):
        return rows.filter(site_id=site) if site else rows
    def can(module,permission):
        return module in enabled and has_permission(user,permission,site if site else ...)
    if can("sales","sales.view"):
        filters = reports.effective("collections",selected)
        rows = reports.collections(user,filters).values("customer_id","customer__name","report_currency").annotate(total=Sum("amount"))
        items = []
        currencies = rows.order_by().values_list("report_currency",flat=True).distinct()
        for currency in currencies:
            for row in rows.filter(report_currency=currency).order_by("-total","customer_id")[:5]:
                items.append({"party":row["customer__name"],"reference":str(row["customer_id"])+"-"+currency,"amount":row["total"],"currency":currency})
        result["customers-activity"]={"items":items}
    if can("supply","supply.view"):
        from supply.views import accessible
        rows = accessible(user).filter(state__in=("in_transit","partial"))
        if site:
            rows = rows.filter(Q(site_id=site)|Q(destination_id=site))
        result["transit"]={"count":rows.count()}
    if can("helpdesk","helpdesk.view"):
        from helpdesk.access import tickets_for
        result["tickets"]={"count":scoped(tickets_for(user)).exclude(status="closed").count()}
    if can("projects","projects.view"):
        from projects.access import accessible
        result["projects"]={"count":scoped(accessible(user)).filter(state="active").count()}
    if can("purchasing","purchasing.view"):
        from purchasing.models import Requisition
        result["requests"]={"count":scoped(visible(Requisition.objects.all(),user,"purchasing.view")).count()}
    if can("inventory","inventory.view"):
        from inventory.models import Receipt
        rows=scoped(visible(Receipt.objects.all(),user,"inventory.view"))
        result["receipts"]={"count":rows.filter(confirmed_at__date__range=(selected["from"],selected["to"])).count()}
    if can("billing_core","billing_core.view"):
        from billing_core.models import Invoice, Receipt
        invoices=scoped(visible(Invoice.objects.filter(space=None,site__active=True),user,"billing_core.view"))
        receipts=scoped(visible(Receipt.objects.filter(space=None,site__active=True),user,"billing_core.view"))
        result["billing"]={"items":list(invoices.order_by("-pk").values("number","status","total","currency")[:5])}
        result["payments"]={"items":list(receipts.filter(created_at__date__range=(selected["from"],selected["to"])).order_by("-created_at").values("number","amount","invoice__currency")[:5])}
    if can("expenses","expenses.view"):
        from expenses.views import dashboard
        result.update(dashboard(user,site))
    if can("bank_reconciliation","bank_reconciliation.view"):
        from decimal import Decimal
        from bank_reconciliation.views import profiles_for
        from bank_reconciliation.models import BankStatement, BankStatementLine
        from accounting.models import Ledger
        profiles = profiles_for(user)
        if site:
            profiles = profiles.filter(site_id=site)
        rows = BankStatement.objects.filter(profile__in=profiles,end__range=(selected["from"],selected["to"])).exclude(status="reconciled")
        totals = rows.annotate(cleared=Sum("lines__amount",filter=Q(lines__match__isnull=False))).values_list("statement_ending_balance","opening_balance","cleared")
        variance = sum((abs(closing-opening-(cleared or 0)) for closing,opening,cleared in totals),Decimal("0.00"))
        result["bank-variance"] = {"amount":str(variance),"currency":Ledger.objects.get(pk=1).currency}
        result["bank-lines"] = {"count":BankStatementLine.objects.filter(statement__in=rows,match=None).count()}
    if can("subscriptions","subscriptions.view"):
        from datetime import date, timedelta
        from django.db.models import F
        from subscriptions.models import Contract,Cycle
        from accounting.models import Ledger
        contracts=scoped(visible(Contract.objects.filter(site__active=True),user,"subscriptions.view"))
        day=timezone.localdate()
        open_cycles=Cycle.objects.filter(contract__in=contracts,invoice__status__in=("issued","partially_paid"),due__lte=day)
        result["subscription-active"]={"count":contracts.filter(status="active").count()}
        result["subscription-trials"]={"count":contracts.filter(status="trial",next_date__range=(day,day+timedelta(days=7))).count()}
        result["subscription-due"]={"count":open_cycles.count()}
        currency=Ledger.objects.get(pk=1).currency
        balance=open_cycles.filter(due__lt=day,invoice__currency=currency).aggregate(n=Sum(F("invoice__total")-F("invoice__paid")))["n"]
        result["subscription-overdue"]={"amount":f"{balance or 0:.2f}","currency":currency}
    return {"widgets":result}
