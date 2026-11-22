from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Max, Q, Sum
from django.db.models.functions import ExtractMonth
from django.utils import timezone
from django.views.decorators.cache import never_cache

from backbone.access import audit, has_permission, scopes, visible
from backbone.http import Problem, body, endpoint, identifier, once, require, site, string
from backbone.models import Site
from backbone.services.csv_export import csv_response
from purchasing.money import decimal_value
from .centers import center_for_site
from .models import Account, BudgetMonth, BudgetVersion, CostCenterScope, Line, Reference
from .services import ledger_lock
from .views import day_field


def scope(user, action="view", site_id=...):
    require(user, "budget.view", site_id)
    if action != "view":
        require(user, "budget." + action, site_id)


def center_data(row):
    settings = getattr(row, "center_scope", None)
    return {"id": row.pk, "code": row.code, "name": row.name,
            "site_id": settings.site_id if settings else None, "active": settings.active if settings else None,
            "revision": settings.revision if settings else 0}


@never_cache
@endpoint("GET", "POST", module="accounting")
def centers(request):
    user = request.user
    scope(user)
    if request.method == "GET":
        allowed = scopes(user, "budget.view")
        query = Reference.objects.filter(ledger_id=1, kind="cost_center").select_related("center_scope")
        if None not in allowed:
            query = query.filter(center_scope__site_id__in=allowed)
        if "page_size" in request.GET:
            from backbone.services.pagination import operational_page
            return operational_page(request,query.order_by("code","pk"),center_data,["code","name"])
        return {"items": [center_data(row) for row in query.order_by("code")]}
    data = body(request)
    site_id = site(data)
    if site_id is None:
        raise Problem("invalid_site")
    scope(user, "centers", site_id)
    code = string(data, "code", 24).upper()
    name = string(data, "name", 120)

    def create():
        ledger = ledger_lock()
        row, created = Reference.objects.get_or_create(ledger=ledger, kind="cost_center", code=code,
                                                        starts=None, defaults={"name": name})
        if not created:
            raise Problem("finance_duplicate_code", 409)
        CostCenterScope.objects.create(reference=row, site_id=site_id)
        audit(user, "cost_center.create", row.code)
        return center_data(row)

    return once(request, "cost_center.create", {"site": site_id, "code": code, "name": name}, create)


@endpoint("PATCH", module="accounting")
def center(request, pk):
    user = request.user
    data = body(request)
    site_id = site(data)
    if site_id is None:
        raise Problem("invalid_site")
    scope(user, "centers", site_id)
    with transaction.atomic():
        ledger_lock()
        row = Reference.objects.filter(pk=pk, ledger_id=1, kind="cost_center").first()
        if not row:
            raise Problem("not_found", 404)
        current = getattr(row, "center_scope", None)
        if current and current.site_id != site_id:
            raise Problem("cost_center_site_locked", 409)
        if not current:
            if not has_permission(user, "budget.centers", None) or not has_permission(user, "budget.view", None):
                raise Problem("forbidden", 403)
            if Line.objects.filter(cost_center=row).exclude(site_id=site_id).exists():
                raise Problem("cost_center_shared_history", 409)
            current = CostCenterScope(reference=row, site_id=site_id, revision=0)
        check_revision(current, data, minimum=0)
        if type(data.get("active")) is not bool:
            raise Problem("invalid_active")
        row.name = string(data, "name", 120)
        row.save(update_fields=["name"])
        current.active = data["active"]
        current.revision += 1
        current.save()
        audit(user, "cost_center.edit", row.code)
        return center_data(row)


def check_revision(row, data, minimum=1):
    revision = data.get("revision")
    if type(revision) is not int or revision < minimum or revision != row.revision:
        raise Problem("revision_conflict", 409)


def budget_record(row):
    return {"id": row.pk, "site_id": row.site_id, "center_id": row.center_id, "account_id": row.account_id,
            "year": row.year, "version": row.version, "state": row.state, "current": row.current,
            "threshold": row.threshold, "reason": row.reason, "revision": row.revision,
            "created_by_id": row.created_by_id, "created_by": row.created_by.get_username(),
            "approved_by_id": row.approved_by_id, "approved_by": row.approved_by.get_username() if row.approved_by_id else None,
            "approved_at": row.approved_at,
            "months": {str(month.month): str(month.amount) for month in row.months.all()}}


def year_value(value):
    if type(value) is not int or not 1900 <= value <= 9998:
        raise Problem("invalid_year")
    return value


def values(data):
    months = data.get("months")
    threshold = data.get("threshold", 80)
    if (not isinstance(months, dict) or not months or set(months) - {str(i) for i in range(1, 13)}
            or type(threshold) is not int or not 1 <= threshold <= 100):
        raise Problem("invalid_budget_values")
    amounts = {int(key): decimal_value(value, 15, 2, "amount") for key, value in months.items()}
    return amounts, threshold, string(data, "reason", 1000, required=False)


def apply_months(row, amounts):
    row.months.all().delete()
    BudgetMonth.objects.bulk_create([BudgetMonth(budget=row, site_id=row.site_id, center_id=row.center_id,
        account_id=row.account_id, year=row.year, version=row.version, month=month, amount=amount)
        for month, amount in sorted(amounts.items())])


def expense(key, active=True):
    rows = Account.objects.filter(pk=key, ledger_id=1, kind="expense", detail=True)
    row = (rows.filter(active=True) if active else rows).first()
    if not row:
        raise Problem("budget_invalid_account")
    return row


@never_cache
@endpoint("GET", "POST", module="accounting")
def budgets(request):
    user = request.user
    scope(user)
    if request.method == "GET":
        rows = visible(BudgetVersion.objects.all(), user, "budget.view").select_related("created_by", "approved_by").prefetch_related("months")
        accounts = Account.objects.filter(ledger_id=1, kind="expense", detail=True).order_by("code")
        if "page_size" in request.GET:
            from backbone.services.pagination import operational_page
            result=operational_page(request,rows.order_by("-year","-pk"),budget_record,["state","account__code","center__code"])
            return {**result,"accounts":list(accounts.values("id","code","name","active"))}
        return {"items": [budget_record(row) for row in rows.order_by("-year", "site_id", "center_id", "account_id", "-version")],
                "accounts": list(accounts.values("id", "code", "name", "active"))}
    data = body(request)
    site_id = site(data)
    if site_id is None:
        raise Problem("invalid_site")
    scope(user, "prepare", site_id)
    year = year_value(data.get("year"))
    amounts, threshold, reason = values(data)

    def create():
        ledger = ledger_lock()
        if ledger.currency != "AED":
            raise Problem("budget_requires_aed", 409)
        center = center_for_site(data.get("center_id"), site_id, required=True)
        account = expense(identifier(data.get("account_id"), "account"))
        dimensions = {"site_id": site_id, "center": center, "account": account, "year": year}
        current = BudgetVersion.objects.filter(**dimensions).aggregate(last=Max("version"))["last"] or 0
        if current and not reason:
            raise Problem("budget_revision_reason_required")
        row = BudgetVersion.objects.create(**dimensions, version=current + 1, created_by=user,
                                           reason=reason, threshold=threshold)
        apply_months(row, amounts)
        audit(user, "budget.create", row.pk)
        return budget_record(row)

    return once(request, "budget.create", data, create)


def find_budget(user, pk, lock=False):
    rows = visible(BudgetVersion.objects.all(), user, "budget.view")
    row = (rows.select_for_update() if lock else rows).filter(pk=pk).first()
    if not row:
        raise Problem("not_found", 404)
    return row


@never_cache
@endpoint("GET", "PATCH", "POST", module="accounting")
def budget(request, pk):
    user = request.user
    if request.method == "GET":
        return budget_record(find_budget(user, pk))
    data = body(request)
    action = "edit" if request.method == "PATCH" else data.get("action")
    if action not in ("edit", "submit", "approve", "return"):
        raise Problem("invalid_action")
    with transaction.atomic():
        ledger_lock()
        row = find_budget(user, pk, lock=True)
        scope(user, "approve" if action in ("approve", "return") else "prepare", row.site_id)
        check_revision(row, data)
        expected = "submitted" if action in ("approve", "return") else "draft"
        if row.state != expected:
            raise Problem("budget_invalid_state", 409)
        if action in ("edit", "submit") and row.created_by_id != user.pk:
            raise Problem("forbidden", 403)
        if action in ("approve", "return") and row.created_by_id == user.pk:
            raise Problem("budget_separate_approver", 403)
        if action in ("edit", "submit", "approve"):
            center_for_site(row.center_id, row.site_id, required=True)
            expense(row.account_id)
        if action == "edit":
            amounts, row.threshold, row.reason = values(data)
            if row.version > 1 and not row.reason:
                raise Problem("budget_revision_reason_required")
            apply_months(row, amounts)
        elif action == "submit":
            row.state = "submitted"
        elif action == "return":
            reason = string(data, "reason", 1000)
            row.state = "draft"
            audit(user, "budget.return_reason", f"{row.pk} / {reason}")
        else:
            current = BudgetVersion.objects.filter(site_id=row.site_id, center_id=row.center_id,
                account_id=row.account_id, year=row.year, current=True)
            if current.filter(version__gte=row.version).exists():
                raise Problem("budget_newer_approved", 409)
            current.update(current=False)
            row.state, row.current, row.approved_by, row.approved_at = "approved", True, user, timezone.now()
        row.revision += 1
        row.save()
        audit(user, "budget." + action, row.pk)
        return budget_record(row)


def report_filters(query):
    keys = {"year", "site", "center", "account", "as_of"}
    if set(query) - keys:
        raise Problem("report_invalid_filters")
    today = timezone.localdate()
    try:
        year = year_value(int(query.get("year") or today.year))
    except ValueError:
        raise Problem("invalid_year") from None
    end = date(year, 12, 31)
    day = day_field(query, "as_of") if query.get("as_of") else min(end, today) if year <= today.year else end
    if day.year != year:
        raise Problem("budget_as_of_year")
    result = {"year": str(year), "as_of": day.isoformat()}
    for key in ("site", "center", "account"):
        value = query.get(key, "")
        if key == "center" and value == "unassigned":
            result[key] = value
        elif value:
            if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or len(value) > 18 or int(value) < 1:
                raise Problem("report_invalid_filters")
            result[key] = str(int(value))
    return result


def report_rows(user, filters, *, export=False):
    scope(user)
    allowed = scopes(user, "budget.view")
    if export:
        require(user, "platform.export")
        export_sites = scopes(user, "platform.export")
        if None not in export_sites:
            allowed = export_sites if None in allowed else allowed & export_sites
    day = date.fromisoformat(filters["as_of"])
    budgets = BudgetMonth.objects.filter(year=day.year, month__lte=day.month, budget__current=True)
    actual = Line.objects.filter(entry__ledger_id=1, entry__posted_at__isnull=False,
        entry__date__range=(day.replace(month=1, day=1), day), account__kind="expense", account__detail=True)
    if None not in allowed:
        budgets, actual = budgets.filter(site_id__in=allowed), actual.filter(site_id__in=allowed)
    if "site" in filters:
        budgets, actual = budgets.filter(site_id=filters["site"]), actual.filter(site_id=filters["site"])
    if "center" in filters:
        if filters["center"] == "unassigned":
            budgets, actual = budgets.none(), actual.filter(cost_center__isnull=True)
        else:
            budgets, actual = budgets.filter(center_id=filters["center"]), actual.filter(cost_center_id=filters["center"])
    if "account" in filters:
        budgets, actual = budgets.filter(account_id=filters["account"]), actual.filter(account_id=filters["account"])
    return day, budgets, actual


ZERO = Decimal("0.00")


def totals_row(site, center, account, month, budgeted, debit, credit, *, threshold=None, budget_id=None):
    actual = debit - credit
    usage = (100 * actual / budgeted) if budgeted else None
    status = ("unbudgeted" if budgeted is None else "over" if actual > budgeted
              else "warning" if usage is not None and threshold is not None and usage >= threshold else "ok")
    return {"site_id": site, "center_id": center, "account_id": account, "month": month,
            "budget": format(budgeted, ".2f") if budgeted is not None else None,
            "debit": format(debit, ".2f"), "credit": format(credit, ".2f"), "actual": format(actual, ".2f"),
            "remaining": format(budgeted - actual, ".2f") if budgeted is not None else None,
            "usage": round(usage, 2) if usage is not None else None, "status": status, "budget_id": budget_id}


def comparison(user, filters, export=False):
    _, budgets, actual = report_rows(user, filters, export=export)
    budget_map = {(row.site_id, row.center_id, row.account_id, row.month): row
                  for row in budgets.select_related("budget").iterator()}
    actual_map = {(row["site_id"], row["cost_center_id"], row["account_id"], row["month"]):
                  (row["debit"] or ZERO, -(row["credit"] or ZERO)) for row in actual
                  .annotate(month=ExtractMonth("entry__date"))
                  .values("site_id", "cost_center_id", "account_id", "month")
                  .annotate(debit=Sum("amount", filter=Q(amount__gt=0)), credit=Sum("amount", filter=Q(amount__lt=0)))}
    rows = []
    for key in sorted(budget_map.keys() | actual_map.keys(), key=lambda x: tuple(value or 0 for value in x)):
        budget = budget_map.get(key)
        debit, credit = actual_map.get(key, (ZERO, ZERO))
        rows.append(totals_row(*key, budget.amount if budget else None, debit, credit,
                               threshold=budget.budget.threshold if budget else None, budget_id=budget.budget_id if budget else None))
    centers = dict(Reference.objects.filter(pk__in={r["center_id"] for r in rows}).values_list("pk", "name"))
    accounts = {r.pk: f"{r.code} · {r.name}" for r in Account.objects.filter(pk__in={r["account_id"] for r in rows})}
    sites = dict(Site.objects.filter(pk__in={r["site_id"] for r in rows}).values_list("pk", "name"))
    for row in rows:
        row.update(site=sites.get(row["site_id"], ""), center=centers.get(row["center_id"], ""),
                   account=accounts.get(row["account_id"], ""))
    return rows


@never_cache
@endpoint("GET", module="accounting")
def report(request):
    filters = report_filters({k:v for k,v in request.GET.items() if k not in ("page","page_size","q")})
    rows = comparison(request.user, filters)
    budgeted = sum((Decimal(row["budget"]) if row["budget"] is not None else ZERO for row in rows), ZERO)
    debit = sum((Decimal(row["debit"]) for row in rows), ZERO)
    credit = sum((Decimal(row["credit"]) for row in rows), ZERO)
    from backbone.services.pagination import paginate_items
    result=paginate_items(request,rows,("account","center","site","status","month")) if "page_size" in request.GET else {"items":rows}
    return {"filters": filters, **result, "ytd": totals_row(None, None, None, None, budgeted, debit, credit)}


@never_cache
@endpoint("GET", module="accounting")
def details(request):
    query = request.GET.dict()
    try:
        size=int(query.pop("page_size",100))
        if size not in (10,25,50,100):raise ValueError
        search=query.pop("q","")[:150]
        month = int(query.pop("month", "0"))
        page = int(query.pop("page", "1"))
    except ValueError:
        raise Problem("report_invalid_filters") from None
    if not 1 <= month <= 12 or not 1 <= page <= 10000:
        raise Problem("report_invalid_filters")
    no_site = query.get("site") == "unassigned"
    if no_site:
        query.pop("site")
    filters = report_filters(query)
    day, budgets, rows = report_rows(request.user, filters)
    if no_site:
        budgets, rows = budgets.none(), rows.filter(site_id__isnull=True)
        filters["site"] = "unassigned"
    if month > day.month:
        raise Problem("report_invalid_filters")
    selected = rows.filter(entry__date__month=month)
    if search:selected=selected.filter(entry__description__icontains=search)
    totals = selected.aggregate(debit=Sum("amount", filter=Q(amount__gt=0)), credit=Sum("amount", filter=Q(amount__lt=0)))
    debit, credit = totals["debit"] or ZERO, -(totals["credit"] or ZERO)
    items = selected.select_related("entry").order_by("entry__date", "entry_id", "pk")
    return {"filters": filters, "month": month, "count": items.count(), "page": page,
            "debit": debit, "credit": credit, "actual": debit - credit,
            "budgets": [{"id": row.budget_id, "version": row.version, "amount": row.amount, "reason": row.budget.reason}
                        for row in budgets.filter(month=month).select_related("budget")],
            "items": [{"entry_id": row.entry_id, "number": row.entry.number, "date": row.entry.date,
                       "account_id": row.account_id, "site_id": row.site_id, "center_id": row.cost_center_id,
                       "description": row.entry.description, "debit": max(row.amount, ZERO), "credit": max(-row.amount, ZERO)}
                      for row in items[(page - 1)*size:page*size]]}


@never_cache
@endpoint("GET", module="accounting")
def export(request):
    filters = report_filters(request.GET.dict())
    rows = comparison(request.user, filters, export=True)
    keys = ("site", "site_id", "center", "center_id", "account", "account_id", "month", "budget",
            "debit", "credit", "actual", "remaining", "usage", "status", "budget_id")
    return csv_response("budget-vs-actual", keys, ([row[key] for key in keys] for row in rows))
