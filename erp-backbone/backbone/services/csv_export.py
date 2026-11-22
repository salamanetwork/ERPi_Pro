import csv
import io
from itertools import islice

from django.http import HttpResponse
from django.views.decorators.cache import never_cache

from assets.models import Asset
from backbone.access import scopes, visible
from backbone.http import Problem, endpoint, require
from backbone.models import Site
from backbone.registry import enabled_modules
from inventory.models import Item, Location, Unit, Warehouse
from inventory.views import receipt_rows
from inventory.setup import item_directory
from backbone.services.pagination import directory_rows, positive_filter
from purchasing.models import Supplier
from purchasing.orders import order_rows, summary as order_data
from purchasing.views import header, requisition_rows
from sales.models import Customer
from sales.views import document_rows, document_status
from workorders.views import work_order_rows
from .inbox import inbox_rows
from .list_data import list_filters

LIMIT = 10_000
DOCUMENTS = {
    "work-orders": ("workorders", work_order_rows,
                    lambda r: {"number": f"WO-{r.pk}", "title": r.title, "status": r.status, "site_id": r.site_id},
                    ("number", "title", "site", "status")),
    "purchase-requests": ("purchasing", requisition_rows, header,
                          ("number", "title", "site", "total", "currency", "status")),
    "purchase-orders": ("purchase_orders", order_rows, order_data,
                        ("number", "title", "supplier_name", "site", "total", "currency", "status")),
    "goods-receipts": ("inventory", receipt_rows,
                      lambda r: {"number": r.number, "order_number": r.order.number, "warehouse": r.warehouse.name,
                                 "status": r.status},
                      ("number", "order_number", "warehouse", "status")),
    "sales-documents": ("sales", document_rows,
                        lambda r: {"number": r.number, "kind": r.kind, "date": r.date, "amount": r.amount,
                                   "customer": r.snapshot.get("customer", {}).get("name", r.customer.name),
                                   "status": document_status(r)},
                        ("number", "kind", "customer", "site", "date", "amount", "status")),
}
MASTERS = {
    "asset": ("assets", Asset, ("tag", "name", "site__name")),
    "unit": ("inventory", Unit, ("code", "name")),
    "item": ("inventory", Item, ("code", "name", "base_unit__code", "active")),
    "warehouse": ("inventory", Warehouse, ("code", "name", "site__name")),
    "location": ("inventory", Location, ("code", "name", "warehouse__code", "warehouse__site__name")),
    "supplier": ("suppliers", Supplier, ("code", "name", "currency_default", "active")),
    "customer": ("customers", Customer, ("code", "name", "active")),
}


def check_module(module, enabled):
    if module not in enabled:
        raise Problem("module_disabled", 404)


def protect(value):
    if value is None:
        return ""
    value = str(value)
    if value.startswith(("\t", "\r", "\n")) or value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def csv_response(name, columns, rows):
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(columns)
    for index, row in enumerate(islice(rows, LIMIT + 1), 1):
        if index > LIMIT:
            raise Problem("csv_export_limit", 400)
        writer.writerow(map(protect, row))
    return HttpResponse("\ufeff" + buffer.getvalue(), content_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})


def document_values(rows, columns, serialize, filters):
    sites = dict(Site.objects.values_list("pk", "name"))
    for row in rows.iterator():
        data = serialize(row)
        data["site"] = sites.get(row.site_id, "")
        if filters.get("archived") == "1":
            data["status"] = "archived"
        yield [data.get(column, "") for column in columns]


@never_cache
@endpoint("GET")
def export(request, screen):
    user = request.user
    require(user, "platform.export")
    allowed = scopes(user, "platform.export")
    if screen in MASTERS:
        params = request.GET.dict()
        record = positive_filter(params, "record")
        allowed_params = {"record"}
        if screen in ("supplier", "customer", "item"):
            allowed_params.update(("q", "status"))
        if screen == "item":
            allowed_params.add("unit")
        if params.keys() - allowed_params:
            raise Problem("invalid_view_filters")
        filters = {}
    else:
        filters = list_filters(screen, request.GET.dict())
    enabled = enabled_modules()
    if screen == "inbox":
        columns = ("number", "title", "site", "action", "role", "remaining")
        sites = dict(Site.objects.values_list("pk", "name"))
        rows = ([item["number"], item["title"], sites.get(item["site_id"], ""),
                 item["action"], item["role"], item.get("remaining")]
                for item in inbox_rows(user, filters) if None in allowed or item["site_id"] in allowed)
    elif screen in DOCUMENTS:
        module, query, serialize, columns = DOCUMENTS[screen]
        check_module(module, enabled)
        records = visible(query(user, filters), user, "platform.export")
        if screen == "goods-receipts":
            records = records.select_related("warehouse")
        rows = document_values(records, columns, serialize, filters)
    elif screen in MASTERS:
        module, model, columns = MASTERS[screen]
        check_module(module, enabled)
        if screen == "supplier":
            from backbone.access import has_permission
            if not any(has_permission(user, key, None) for key in ("suppliers.view", "suppliers.manage")):
                raise Problem("forbidden", 403)
        else:
            require(user, "sales.view" if screen == "customer" else module + ".view")
        records = model.objects.order_by("tag" if screen == "asset" else "code", "pk")
        if screen == "item":
            records = item_directory(params)
        elif screen in ("supplier", "customer"):
            records = directory_rows(records, params)
        elif record:
            records = records.filter(pk=record)
        if screen in ("asset", "warehouse"):
            records = visible(visible(records, user, module + ".view"), user, "platform.export")
        elif screen == "location":
            warehouses = visible(visible(Warehouse.objects.all(), user, "inventory.view"), user, "platform.export")
            records = records.filter(warehouse__in=warehouses)
        else:
            require(user, "platform.export", None)
        rows = records.values_list(*columns).iterator()
    else:
        raise Problem("not_found", 404)
    return csv_response(screen, columns, rows)
