from collections import defaultdict
from datetime import date, timedelta

from django.db.models import Case, F, Sum, When
from django.db.models.functions import TruncMonth, TruncWeek

from backbone.access import has_permission
from backbone.http import Problem
from . import reports
from .report_filters import AGING_BUCKETS, valid_filters

POSTING_ENTRY = "postingevent__posting__entry"
POSTING_DATE = POSTING_ENTRY + "__date"
POSTING_CURRENCY = POSTING_ENTRY + "__ledger__currency"
PERIODS = ("day", "week", "month")


def filters_for_dashboard(query, today, *, charts=False):
    allowed = {"site", "from", "to", "as_of"}
    if charts:
        allowed.add("group")
    if set(query.keys()) - allowed:
        raise Problem("report_invalid_filters")
    selected = valid_filters("sales", {key: query.get(key, "") for key in ("site", "from", "to")})
    end = date.fromisoformat(selected["to"]) if "to" in selected else today
    selected.setdefault("to", end.isoformat())
    selected.setdefault("from", date.fromordinal(max(1, end.toordinal() - 29)).isoformat())
    selected.update(valid_filters("finance", {"as_of": query.get("as_of", "")}))
    selected.setdefault("as_of", today.isoformat())
    valid_filters("sales", {key: selected[key] for key in ("from", "to")})
    if charts:
        span = (end - date.fromisoformat(selected["from"])).days + 1
        group = query.get("group", "auto") or "auto"
        if group == "auto":
            group = "day" if span <= 60 else "week" if span <= 365 else "month"
        if group not in PERIODS or (group == "day" and span > 366):
            raise Problem("report_invalid_range")
        selected["group"] = group
    return selected


def period_filters(selected):
    return {key: selected[key] for key in ("site", "from", "to") if key in selected}


def as_of_filters(selected):
    return {key: selected[key] for key in ("site", "as_of") if key in selected}


def eligible(user, metric, enabled, selected):
    if not reports.available(user, metric, enabled):
        return False
    if metric == "ap-aging" and "supplier_invoices" not in enabled:
        return False
    if "site" not in selected:
        return True
    site = int(selected["site"])
    module, permission = reports.METRIC_ACCESS[metric]
    if permission == "accounting.view":
        return True
    return has_permission(user, permission, site) and (
        module != "workorders" or has_permission(user, "workorders.view", site))


def signed_sales():
    return Case(When(kind="credit", then=-F("amount")), default=F("amount"))


def sales_totals(user, filters):
    rows = reports.posted_sales(user, filters).order_by().annotate(
        currency=F(POSTING_CURRENCY))
    return {row["currency"]: format(row["total"], ".2f") for row in
            rows.values("currency").annotate(total=Sum(signed_sales())).order_by("currency")}


def date_group(field, group):
    if group == "week":
        return TruncWeek(field)
    if group == "month":
        return TruncMonth(field)
    return F(field)


def period_bounds(day, group):
    if group == "week":
        return day - timedelta(days=day.weekday()), date.fromordinal(min(date.max.toordinal(), day.toordinal() + (6 - day.weekday())))
    if group == "month":
        month_end = date(day.year + (day.month == 12), day.month % 12 + 1, 1) - timedelta(days=1) if day < date(9999, 12, 1) else date.max
        return day.replace(day=1), month_end
    return day, day


def windows(filters, group):
    start, end = date.fromisoformat(filters["from"]), date.fromisoformat(filters["to"])
    day = start
    while day <= end:
        key, through = period_bounds(day, group)
        last = min(through, end)
        yield key, {**filters, "from": day.isoformat(), "to": last.isoformat()}
        if last == end:
            break
        day = last + timedelta(days=1)


def trends(user, selected):
    filters = period_filters(selected)
    group = selected["group"]
    series = []
    groups = list(windows(filters, group))
    for metric, rows, date_field, currency, total in (
        ("net-posted-sales", reports.posted_sales(user, filters), POSTING_DATE, POSTING_CURRENCY, signed_sales()),
        ("collections", reports.collections(user, filters), "date", "report_currency", F("amount")),
    ):
        output = defaultdict(dict)
        amounts = rows.order_by().values(currency, start=date_group(date_field, group)).annotate(total=Sum(total))
        for row in amounts:
            output[row[currency]][row["start"]] = row["total"]
        for code, values in sorted(output.items()):
            series.append({"metric": metric, "currency": code, "points": [
                {"from": span["from"], "to": span["to"], "value": format(values.get(key, reports.ZERO), ".2f"),
                 "filters": {**span, "currency": code}} for key, span in groups]})
    return series


def aging(user, metric, filters):
    amounts = defaultdict(dict)
    if metric == "ap-aging":
        for row in reports.ap_buckets(user, filters):
            amounts[row["currency"]][row["group"]] = row["amount"]
    else:
        docs, invoices = reports.ar_documents(user, filters, ())
        settled = reports.ar_settled(docs)
        day = date.fromisoformat(filters["as_of"])
        for key, due, total, currency in invoices.order_by().values_list("pk", "due", "amount", POSTING_CURRENCY).iterator():
            remaining = total - settled.get(key, reports.ZERO)
            if remaining:
                label = reports.bucket(day, due, remaining)
                column = amounts[currency]
                column[label] = column.get(label, reports.ZERO) + remaining
    return {"filters": filters, "series": [
        {"currency": code, "total": format(sum(values.values(), reports.ZERO), ".2f"),
         "points": [{"bucket": bucket, "value": format(values.get(bucket, reports.ZERO), ".2f"),
                     "filters": {**filters, "currency": code, "bucket": bucket}}
                    for bucket in AGING_BUCKETS]} for code, values in sorted(amounts.items())]}
