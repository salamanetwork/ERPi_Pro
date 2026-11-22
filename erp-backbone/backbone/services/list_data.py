"""Shared list filters for saved views, JSON lists and CSV exports."""

from backbone.http import Problem

SALES_KINDS = ("quote", "order", "delivery", "invoice", "receipt", "return", "credit")
FILTERS = {
    "inbox": {"kind": ("pr", "wo", "po", "grn", "sales", "si", "hr", "payroll"), "site": None,
              "action": ("review", "issue", "confirm", "confirm_order", "invoice", "post_invoice", "execute_maintenance", "close_maintenance", "approve_payroll", "approve_leave",
                         "approve_timesheet", "review_attendance_correction")},
    "work-orders": {"review": ("mine",)},
    "purchase-requests": {"review": ("mine",), "archived": ("1",)},
    "purchase-orders": {"archived": ("1",), "site": None, "ready": ("1",),
                        "status": ("draft", "issued", "cancelled"), "q": "text"},
    "goods-receipts": {"archived": ("1",)},
    "sales-documents": {"kind": SALES_KINDS},
}


def list_filters(screen, data):
    rules = FILTERS.get(screen, {})
    if not isinstance(data, dict) or any(key not in rules for key in data):
        raise Problem("invalid_view_filters")
    filters = {}
    for key, value in data.items():
        if value == "":
            continue
        allowed = rules[key]
        if not isinstance(value, str):
            raise Problem("invalid_view_filters")
        if allowed is None:
            if not value.isascii() or not value.isdecimal() or len(value) > 18 or int(value) < 1:
                raise Problem("invalid_view_filters")
            value = str(int(value))
        elif allowed == "text":
            if len(value.strip()) > 120:
                raise Problem("invalid_view_filters")
            value = value.strip()
        elif value not in allowed:
            raise Problem("invalid_view_filters")
        filters[key] = value
    if "review" in filters and "archived" in filters:
        raise Problem("invalid_view_filters")
    return filters
