from django.utils import timezone
from django.views.decorators.cache import never_cache

from backbone.access import has_permission, preload_scopes
from backbone.http import endpoint
from backbone.registry import enabled_modules
from .experience import unread_notice_count
from .inbox import actionable, approval_tasks, issuable_orders
from . import reports, workspace_charts as charts


@never_cache
@endpoint("GET")
def counters(request):
    user = request.user
    preload_scopes(user)
    enabled = enabled_modules()
    approval_keys = list(approval_tasks(user, enabled, keys_only=True))
    return {"inbox": sum(actionable(user, enabled, count_only=True, approval_count=len(approval_keys))),
            "unread": unread_notice_count(user, enabled, approval_keys)}


@never_cache
@endpoint("GET")
def dashboard(request):
    user = request.user
    preload_scopes(user)
    enabled = enabled_modules()
    today = timezone.localdate()
    selected = charts.filters_for_dashboard(request.GET, today)
    site = int(selected["site"]) if "site" in selected else None
    filters = {"site": str(site)} if site is not None else {}
    result = {"inbox": sum(actionable(user, enabled, site=site, count_only=True)),
              "filters": selected, "today": today.isoformat(), "cards": {}}
    if "purchase_orders" in enabled and all(
            has_permission(user, permission, site if site is not None else ...)
            for permission in ("purchasing.view", "purchasing.po.issue")):
        result["ready_purchase_orders"] = issuable_orders(user, site).count()
    if "sales" in enabled and has_permission(user, "sales.view"):
        # Preserve the original current-month fields for existing callers.
        month = {"from": today.replace(day=1).isoformat(), "to": today.isoformat()}
        totals = charts.sales_totals(user, {**filters, **month})
        result.update(sales=totals, period=month)
        if charts.eligible(user, "net-posted-sales", enabled, selected):
            period = charts.period_filters(selected)
            values = totals if period == {**filters, **month} else charts.sales_totals(user, period)
            result["cards"]["net-posted-sales"] = {"filters": period, "currencies": values}
    if "crm" in enabled:
        from crm.services import dashboard as crm_dashboard

        if (summary := crm_dashboard(user, site)) is not None:
            result["crm"] = summary
    if charts.eligible(user, "grni", enabled, selected):
        row = reports.summarize(user, "grni", charts.as_of_filters(selected))
        result["cards"]["grni"] = {"filters": row["filters"], "currencies": row["currencies"]}
    return result


@never_cache
@endpoint("GET")
def dashboard_charts(request):
    user = request.user
    preload_scopes(user)
    enabled = enabled_modules()
    selected = charts.filters_for_dashboard(request.GET, timezone.localdate(), charts=True)
    result = {"filters": selected, "widgets": {}}
    widgets = result["widgets"]
    if charts.eligible(user, "net-posted-sales", enabled, selected):
        widgets["sales"] = charts.trends(user, selected)
    for metric in ("ap-aging", "ar-aging"):
        if charts.eligible(user, metric, enabled, selected):
            widgets[metric] = charts.aging(user, metric, charts.as_of_filters(selected))
    if charts.eligible(user, "pm-on-time", enabled, selected):
        row = reports.summarize(user, "pm-on-time", charts.period_filters(selected))
        widgets["pm-on-time"] = {key: row[key] for key in ("filters", "count", "on_time")}
    return result
