from decimal import Decimal

from django.db import IntegrityError, transaction

from .access import audit
from .extensions import site_blockers
from .http import Problem, body, endpoint, identifier, string
from .models import Assignment, Site
from .services.documents import archive_rows


def require_active_site(pk, *, lock=False):
    if pk is None:
        return
    rows = Site.objects.select_for_update() if lock else Site.objects.all()
    row = rows.filter(pk=pk).first()
    if row is None:
        raise Problem("invalid_site")
    if not row.active:
        raise Problem("site_inactive", 409)


def blockers(site):
    from django.db.models import Q

    from accounting.models import SupplierInvoice
    from hr.models import AttendanceSession, Leave, PayrollRun, WeeklyTimesheet
    from hr.payroll import aggregates
    from inventory.models import Receipt, Warehouse
    from inventory.views import line_progress
    from purchasing.models import PurchaseOrder, Requisition
    from sales.models import Document
    from sales.views import consumed, order_progress
    from workorders.models import WorkOrder
    from supply.models import Transfer

    orders = PurchaseOrder.objects.filter(site=site).exclude(pk__in=archive_rows("po").values("object_id"))
    sales = Document.objects.filter(site=site, kind="order", status="confirmed")
    unbilled_orders = set()
    for delivery in Document.objects.filter(site=site, kind="delivery").iterator():
        billed = list(delivery.children.filter(Q(kind="invoice") | Q(kind="return", snapshot__credited=False)))
        if any(Decimal(line["quantity"]) > consumed(billed, "delivery_line_id", line["id"]) for line in delivery.lines):
            unbilled_orders.add(delivery.order_id)

    checks = {
        **site_blockers(site.pk),
        "assignments": Assignment.objects.filter(site=site).count(),
        "stock_transfers": Transfer.objects.filter(Q(site=site) | Q(destination=site))
            .exclude(state__in=("cancelled", "received")).count(),
        "warehouses": Warehouse.objects.filter(site=site, active=True).count(),
        "purchase_requests": Requisition.objects.filter(site=site, status__in=("draft", "in_review"))
            .exclude(pk__in=archive_rows("pr").values("object_id")).count(),
        "purchase_orders": orders.filter(status="draft").count() + sum(
            any(Decimal(line["remaining"]) > 0 for line in line_progress(row))
            for row in orders.filter(status="issued").iterator()),
        "goods_receipts": Receipt.objects.filter(site=site, status="draft")
            .exclude(pk__in=archive_rows("grn").values("object_id")).count(),
        "work_orders": WorkOrder.objects.filter(site=site).filter(
            Q(maintenance__isnull=True) & ~Q(status="approved") |
            Q(maintenance__isnull=False) & ~Q(maintenance__stage__in=("closed", "cancelled"))).count(),
        "sales_orders": Document.objects.filter(site=site, kind="order", status="draft").count() + sum(
            row.pk in unbilled_orders or any(Decimal(line["remaining"]) > 0 for line in order_progress(row))
            for row in sales.iterator()),
        "supplier_invoices": SupplierInvoice.objects.filter(site=site, status="draft")
            .exclude(pk__in=archive_rows("si").values("object_id")).count(),
        "payroll_runs": PayrollRun.objects.filter(site=site, state__in=("draft", "submitted", "approved")).count() + sum(
            Decimal(aggregates(row)["outstanding"]) > 0 for row in PayrollRun.objects.filter(site=site, state="posted").iterator()),
        "leave_requests": Leave.objects.filter(site=site, state="pending").count(),
        "attendance_sessions": AttendanceSession.objects.filter(site=site, ended_at__isnull=True).count(),
        "weekly_timesheets": WeeklyTimesheet.objects.filter(site=site, state="submitted").count(),
    }
    return {key: count for key, count in checks.items() if count}


def record(row):
    return {"id": row.pk, "name": row.name, "description": row.description, "active": row.active, "revision": row.revision}


@endpoint("GET", admin=True)
def sites(request):
    if "page_size" in request.GET:
        from backbone.services.pagination import operational_page
        return {**operational_page(request,Site.objects.order_by("name","pk"),record,['name', 'description'])}
    return {"items": [record(row) for row in Site.objects.order_by("name")]}


@endpoint("GET", "PATCH", admin=True)
def site_detail(request, pk):
    if request.method == "GET":
        row = Site.objects.filter(pk=pk).first()
        if row is None:
            raise Problem("not_found", 404)
        return {**record(row), "blockers": blockers(row) if row.active else {}}
    data = body(request)
    name = string(data, "name", 120)
    active = data.get("active")
    if type(active) is not bool:
        raise Problem("invalid_site")
    try:
        with transaction.atomic():
            row = Site.objects.select_for_update().filter(pk=pk).first()
            if row is None:
                raise Problem("not_found", 404)
            if row.revision != identifier(data.get("revision"), "revision"):
                raise Problem("revision_conflict", 409)
            if row.active and not active and (blocking := blockers(row)):
                return {"error": "site_has_open_records", "blockers": blocking}, 409
            action = ("activate" if active else "deactivate") if active != row.active else "update"
            if "description" in data:
                row.description = string(data, "description", 500, required=False)
            row.name, row.active = name, active
            row.revision += 1
            row.save(update_fields=["name", "active", "revision", "description"])
            audit(request.user, "site." + action, row.pk)
    except IntegrityError:
        raise Problem("duplicate_name", 409) from None
    return record(row)
