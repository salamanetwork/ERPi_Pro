from datetime import date, timedelta

from django.utils import timezone

from backbone.http import Problem

BOARDS = {
    "finance": ("ap-aging", "ar-aging", "grni", "pending-postings"),
    "stock": ("open-purchases", "overdue-purchases", "undated-purchases", "stock-value"),
    "sales": ("deliveries", "sales-invoices", "net-posted-sales", "collections", "unbilled-sales"),
    "maintenance": ("pm-due", "maintenance-open", "pm-on-time", "maintenance-parts", "maintenance-posted"),
}
METRICS = {metric: board for board, metrics in BOARDS.items() for metric in metrics}
AGING = ("ap-aging", "ar-aging")
AGING_BUCKETS = ("current", "1-30", "31-60", "61-90", "90+", "credit")
AS_OF = {"ap-aging", "ar-aging", "grni"}
RANGE = {"deliveries", "sales-invoices", "net-posted-sales", "collections", "pm-on-time", "maintenance-parts", "maintenance-posted"}
FILTERS = {
    "finance": ("site", "as_of", "supplier", "customer", "account"),
    "stock": ("site", "supplier", "item"),
    "sales": ("site", "from", "to", "customer"),
    "maintenance": ("site", "from", "to", "asset"),
}
RELEVANT = {
    "ap-aging": ("supplier", "account"), "ar-aging": ("customer", "account"), "grni": ("supplier", "account"),
    "pending-postings": (),
    "open-purchases": ("supplier",), "overdue-purchases": ("supplier",), "undated-purchases": ("supplier",),
    "stock-value": ("item",), "deliveries": ("customer",), "sales-invoices": ("customer",), "net-posted-sales": ("customer",),
    "collections": ("customer",), "unbilled-sales": ("customer",),
    "pm-due": ("asset",), "maintenance-open": ("asset",), "pm-on-time": ("asset",),
    "maintenance-parts": ("asset",), "maintenance-posted": ("asset",),
}


def valid_filters(board, values, *, defaults=False, metric=None):
    if board not in FILTERS:
        raise Problem("not_found", 404)
    allowed = set(FILTERS[board])
    if metric in AGING:
        allowed.add("bucket")
    if metric in (*AGING, "net-posted-sales", "collections"):
        allowed.add("currency")
    if not isinstance(values, dict) or set(values) - allowed:
        raise Problem("report_invalid_filters")
    result = {}
    for key, value in values.items():
        if not isinstance(value, str):
            raise Problem("report_invalid_filters")
        if not value:
            continue
        if key in ("as_of", "from", "to"):
            try:
                parsed = date.fromisoformat(value)
                if parsed.isoformat() != value:
                    raise ValueError
            except ValueError:
                raise Problem("report_invalid_filters") from None
            result[key] = value
        elif key == "bucket" and value in AGING_BUCKETS:
            result[key] = value
        elif key == "currency" and len(value) == 3 and value.isascii() and value.isalpha() and value.isupper():
            result[key] = value
        elif key not in ("bucket", "currency") and value.isascii() and value.isdecimal() and len(value) <= 18 and int(value) > 0:
            result[key] = str(int(value))
        else:
            raise Problem("report_invalid_filters")
    today = timezone.localdate()
    if defaults and "as_of" in FILTERS[board]:
        result.setdefault("as_of", today.isoformat())
    if defaults and "from" in FILTERS[board]:
        selected = date.fromisoformat(result.get("to", today.isoformat()))
        result.setdefault("to", selected.isoformat())
        result.setdefault("from", selected.replace(day=1).isoformat())
    if "from" in result and "to" in result:
        duration = date.fromisoformat(result["to"]) - date.fromisoformat(result["from"])
        if not timedelta(0) <= duration <= timedelta(days=731):
            raise Problem("report_invalid_range")
    return result
