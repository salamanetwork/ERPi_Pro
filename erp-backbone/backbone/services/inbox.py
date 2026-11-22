from decimal import Decimal
from itertools import islice

from django.db.models import Prefetch, Q
from django.utils import timezone
from django.views.decorators.cache import never_cache

from backbone.access import has_permission, scopes as permission_scopes, visible
from backbone.http import Problem, endpoint
from backbone.models import ExperienceNotice, InboxTask
from backbone.registry import enabled_modules
from backbone.review_access import reviewer_scopes
from .documents import archive_rows, definition


def open_task(kind, object_id, step_key, role_id):
    task, _ = InboxTask.objects.get_or_create(
        kind=kind, object_id=object_id, step_key=step_key, defaults={"role_id": role_id},
    )
    if task.role_id != role_id:
        raise ValueError("Inbox step key already belongs to another role")
    return task


def close_task(kind, object_id, step_key):
    InboxTask.objects.filter(kind=kind, object_id=object_id, step_key=step_key, closed_at__isnull=True).update(
        closed_at=timezone.now(),
    )


def approval_outcome(user_id, kind, pk, run_id, step, action):
    ExperienceNotice.objects.get_or_create(user_id=user_id, key=f"{kind}:{pk}:{run_id}:{step}:{action}",
        defaults={"kind": kind, "object_id": pk, "event": action})


def task(kind, row, action, key, route, number, title, role=None):
    return {"id": key, "kind": kind, "site_id": row.site_id, "action": action, "number": number,
            "title": title, "route": route, "role": role}


def approval_tasks(user, enabled, *, site=None, keys_only=False):
    from purchasing.models import Review
    from purchasing.views import authority
    from workorders.approvals import review_authority
    from workorders.models import ApprovalRun

    if "purchasing" in enabled and has_permission(user, "purchasing.approve"):
        rows = Review.objects.filter(status="pending", requisition__status="in_review").exclude(submitter=user).exclude(
            requisition_id__in=archive_rows("pr").values("object_id")).select_related("requisition").order_by("-id")
        pr_sites = permission_scopes(user, "purchasing.view")
        scopes = reviewer_scopes(user, "purchasing")
        if None not in pr_sites:
            rows = rows.filter(requisition__site_id__in=pr_sites)
        if site is not None:
            rows = rows.filter(requisition__site_id=site)
        for run in rows.iterator():
            row = run.requisition
            if row.site_id in pr_sites or None in pr_sites:
                if not authority(user, row, run, scopes)[0]:
                    continue
                if keys_only:
                    yield f"pr:{row.pk}:{run.pk}:{run.next_step}"
                    continue
                yield task("pr", row, "review", f"pr:{row.pk}:{run.pk}:{run.next_step}", f"/purchase-requests/{row.pk}",
                           row.document_number, row.title, run.steps[run.next_step]["name"])

    if "workorders" in enabled and has_permission(user, "workorders.approve"):
        rows = ApprovalRun.objects.filter(outcome="pending", order__status="in_review").exclude(submitted_by=user).select_related(
            "order", "template").order_by("-id")
        wo_sites = permission_scopes(user, "workorders.view")
        scopes = reviewer_scopes(user, "workorders")
        if None not in wo_sites:
            rows = rows.filter(order__site_id__in=wo_sites)
        if site is not None:
            rows = rows.filter(order__site_id=site)
        for run in rows.iterator():
            row = run.order
            if (row.site_id in wo_sites or None in wo_sites) and review_authority(user, run, scopes)[0]:
                if keys_only:
                    yield f"wo:{row.pk}:{run.pk}:{run.next_step}"
                    continue
                yield task("wo", row, "review", f"wo:{row.pk}:{run.pk}:{run.next_step}", f"/work-orders/{row.pk}",
                           f"WO-{row.pk}", row.title, run.template.steps[run.next_step]["name"])


def issuable_orders(user, site=None):
    from purchasing.models import PurchaseOrder

    rows = PurchaseOrder.objects.filter(status="draft", supplier__active=True, total__gt=0)
    rows = visible(visible(rows, user, "purchasing.view"), user, "purchasing.po.issue")
    rows = rows.exclude(pk__in=archive_rows("po").values("object_id"))
    return rows if site is None else rows.filter(site_id=site)


def actionable(user, enabled, *, site=None, count_only=False, approval_count=None):
    from accounting.models import Ledger
    from inventory.models import OrderLine
    from sales.models import Document
    from sales.views import consumed
    from workorders.models import WorkOrder

    if approval_count is None:
        for row in approval_tasks(user, enabled, site=site, keys_only=count_only):
            yield 1 if count_only else row
    else:
        yield approval_count

    if "crm" in enabled and has_permission(user, "crm.view"):
        from crm import services as crm

        for rows, action, route in ((crm.due(user, site), "crm_activity_due", "/crm/activities"),
                                    (crm.pending(user, site), "crm_customer_review", "/crm/customer-requests")):
            if count_only:
                yield rows.count()
            else:
                for row in rows.order_by("-pk").iterator():
                    title = row.title if action == "crm_activity_due" else row.fields["name"]
                    yield task("crm", row, action, f"crm:{action}:{row.pk}", f"{route}/{row.pk}", row.number, title)

    for kind, action, permission in (("po", "issue", "purchasing.po.issue"), ("grn", "confirm", "inventory.confirm")):
        spec = definition(kind)
        if spec.module not in enabled or kind == "grn" and "purchase_orders" not in enabled or not has_permission(user, permission):
            continue
        if kind == "po":
            rows = issuable_orders(user, site)
            if count_only:
                yield rows.count()
                continue
        else:
            rows = visible(spec.model.objects.filter(status="draft"), user, f"{spec.module}.view")
            rows = visible(rows, user, permission).exclude(pk__in=archive_rows(kind).values("object_id"))
            rows = visible(rows.filter(order__status="issued", order__currency="AED"), user, "purchasing.view").select_related("order")
            if site is not None:
                rows = rows.filter(site_id=site)
        if kind == "grn":
            rows = rows.prefetch_related(Prefetch("order__orderline_set",
                queryset=OrderLine.objects.only("order_id", "line_id", "received"),
                to_attr="inbox_received_lines"))
        for row in rows.order_by("-id").iterator(chunk_size=250):
            if kind == "grn":
                received = {line.line_id: line.received for line in row.order.inbox_received_lines}
                remaining = {line["id"]: Decimal(line["quantity"]) - received.get(line["id"], 0)
                             for line in row.order.snapshot.get("lines", [])}
                for line in row.lines:
                    key = line["order_line_id"]
                    remaining[key] = remaining.get(key, Decimal(0)) - Decimal(line["quantity"])
                if not row.lines or any(value < 0 for value in remaining.values()):
                    continue
            yield 1 if count_only else task(kind, row, action, f"{kind}:{row.pk}:{action}", f"{spec.route}/{row.pk}",
                       row.document_number, row.title if kind == "po" else row.order.title)
    if {"assets", "workorders"}.issubset(enabled):
        from workorders.models import MaintenanceJob

        orders = visible(visible(WorkOrder.objects.all(), user, "workorders.view"), user, "maintenance.view")
        if site is not None:
            orders = orders.filter(site_id=site)
        jobs = MaintenanceJob.objects.filter(order__in=orders).select_related("order")
        execute = permission_scopes(user, "maintenance.execute")
        supervise = permission_scopes(user, "maintenance.supervise")
        if count_only:
            executing = jobs.filter(assignee=user, stage__in=("waiting", "in_progress"), order__status="approved")
            closing = jobs.filter(stage="completed")
            if None not in execute:
                executing = executing.filter(order__site_id__in=execute)
            if None not in supervise:
                closing = closing.filter(order__site_id__in=supervise)
            yield (executing | closing).count()
            jobs = jobs.none()
        for job in jobs.filter(Q(assignee=user, stage__in=("waiting", "in_progress"), order__status="approved")
                               | Q(stage="completed")).order_by("-id"):
            row = job.order
            close = job.stage == "completed"
            allowed = supervise if close else execute
            if row.site_id in allowed or None in allowed:
                action = "close_maintenance" if close else "execute_maintenance"
                yield 1 if count_only else task("wo", row, action, f"wo:{row.pk}:{action}", f"/work-orders/{row.pk}",
                           f"WO-{row.pk}", row.title)
    if "supplier_invoices" in enabled and has_permission(user, "ap.view") and has_permission(user, "ap.post"):
        from accounting.payables import posting_drafts

        invoices = posting_drafts(user)
        if site is not None:
            invoices = invoices.filter(site_id=site)
        if count_only:
            yield invoices.count()
        else:
            for row in invoices.select_related("supplier").order_by("-id"):
                yield task("si", row, "post_invoice", f"si:{row.pk}:post", f"/payables/invoices/{row.pk}",
                           row.document_number, row.supplier.name)
    if "payroll" in enabled:
        from hr.access import allowed_rows
        from hr.models import PayrollRun

        reviews = allowed_rows(PayrollRun.objects.filter(state="submitted"), user, "payroll.approve").exclude(prepared_by=user)
        if site is not None:
            reviews = reviews.filter(site_id=site)
        if count_only:
            yield reviews.count()
        else:
            for row in reviews.order_by("-pk"):
                yield task("payroll", row, "approve_payroll", f"hr:payroll:{row.pk}", f"/payroll/runs/{row.pk}",
                           f"PAY-{row.pk}", "Payroll run")
    if "hr" in enabled:
        from hr.access import allowed_rows
        from hr.attendance import pending_reviews
        from hr.attendance_review import pending_corrections
        from hr.models import Leave

        requests = allowed_rows(Leave.objects.filter(state="pending"), user, "hr.leave.approve")
        requests = requests.exclude(Q(requested_by=user) | Q(employee__user=user))
        if site is not None:
            requests = requests.filter(site_id=site)
        if count_only:
            yield requests.count()
            if has_permission(user, "attendance.approve"):
                yield pending_reviews(user, site).count()
            if has_permission(user, "attendance.correct"):
                yield pending_corrections(user, site).count()
        else:
            for row in requests.order_by("-pk"):
                yield task("hr", row, "approve_leave", f"hr:leave:{row.pk}", f"/hr/leaves/{row.pk}",
                           f"LV-{row.pk}", "Leave request")
            if has_permission(user, "attendance.approve"):
                for row in pending_reviews(user, site).select_related("employee").order_by("-pk"):
                    yield task("hr", row, "approve_timesheet", f"hr:timesheet:{row.pk}",
                               f"/hr/attendance/{row.pk}", f"TS-{row.pk}", row.employee.name)
            if has_permission(user, "attendance.correct"):
                for row in pending_corrections(user, site).select_related("session__employee").order_by("-pk"):
                    yield task("hr", row, "review_attendance_correction", f"hr:correction:{row.pk}",
                               f"/hr/attendance/corrections/{row.pk}", f"TC-{row.pk}", row.session.employee.name)
    if "sales" not in enabled:
        return
    confirm_sites = permission_scopes(user, "sales.order")
    invoice_sites = permission_scopes(user, "sales.invoice")
    if not confirm_sites and not invoice_sites:
        return
    ledger = invoice_sites and Ledger.objects.filter(pk=1, activated_at__isnull=False).exists()
    rows = visible(Document.objects.all(), user, "sales.view")
    if site is not None:
        rows = rows.filter(site_id=site)
    orders = rows.filter(kind="order", status="draft", customer__active=True)
    if None not in confirm_sites:
        orders = orders.filter(site_id__in=confirm_sites)
    deliveries = rows.filter(kind="delivery", status="confirmed") if ledger else rows.none()
    if None not in invoice_sites:
        deliveries = deliveries.filter(site_id__in=invoice_sites)
    if count_only:
        yield orders.count()
        orders = orders.none()
    candidates = (orders | deliveries).select_related("customer").prefetch_related(
        Prefetch("children", queryset=Document.objects.filter(Q(kind="invoice") | Q(kind="return", snapshot__credited=False))
                 .only("source_id", "lines"), to_attr="inbox_billed"))
    for row in candidates.order_by("-id").iterator(chunk_size=250):
        action = "confirm_order" if row.kind == "order" else "invoice"
        sites = confirm_sites if row.kind == "order" else invoice_sites
        if None not in sites and row.site_id not in sites:
            continue
        remaining = None
        if row.kind == "delivery":
            if not ledger:
                continue
            billed = row.inbox_billed
            remaining = sum(Decimal(line["quantity"]) - consumed(billed, "delivery_line_id", line["id"])
                            for line in row.lines)
            if remaining <= 0:
                continue
        if count_only:
            yield 1
            continue
        result = task("sales", row, action, f"sales:{row.pk}:{action}", f"/sales/{row.pk}",
                      row.number, row.customer.name)
        if remaining is not None:
            result["remaining"] = format(remaining, ".3f")
        yield result


def inbox_rows(user, filters):
    kind, site, action = (filters.get(key, "") for key in ("kind", "site", "action"))
    if site and (not site.isascii() or not site.isdecimal() or len(site) > 18):
        raise Problem("invalid_site")
    items = actionable(user, enabled_modules(), site=int(site) if site else None)
    return (row for row in items if (not kind or row["kind"] == kind) and (not action or row["action"] == action))


def activity_limit(filters):
    raw = filters.get("limit", "")
    if not raw:
        return None
    if not raw.isascii() or not raw.isdecimal() or len(raw) > 2 or not 1 <= int(raw) <= 20:
        raise Problem("report_invalid_filters")
    return int(raw)


@never_cache
@endpoint("GET")
def inbox(request):
    if "page_size" in request.GET:
        from .pagination import paginate_items
        rows = list(inbox_rows(request.user, {}))
        facets = {key: sorted({str(x[key]) for x in rows if x.get(key) is not None}) for key in ("kind","action","site_id")}
        rows = [row for row in rows if all(not request.GET.get(key) or str(row[field]) == request.GET[key]
                for key,field in (("kind","kind"),("action","action"),("site","site_id")))]
        return {**paginate_items(request,rows,("number","title","action","role")), "facets":facets}
    rows = inbox_rows(request.user, request.GET)
    limit = activity_limit(request.GET)
    return {"items": list(islice(rows, limit) if limit is not None else rows)}
