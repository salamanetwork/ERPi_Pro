from django.db import transaction
from django.utils import timezone
from assets.services import reference, selectable_assets
from backbone.access import audit, visible
from backbone.http import Problem, body, endpoint, identifier, once, require, site, string
from .approvals import apply_decision, can_review, review_details, reviewer_scopes, start_approval
from .models import ApprovalRun, WorkOrder


def serialize(row):
    data = {key: getattr(row, key) for key in (
        "id", "title", "description", "site_id", "asset_reference", "asset_snapshot",
        "status", "revision", "created_at", "submitted_at",
    )}
    for key in ("created_at", "submitted_at"):
        data[key] = data[key].isoformat() if data[key] else None
    if hasattr(row, "maintenance"):
        data["maintenance_stage"] = row.maintenance.stage
    return data


def fields_from(data):
    return {"title": string(data, "title", 120),
            "description": string(data, "description", 5000, required=False), "site_id": site(data)}


def set_asset(user, row, data):
    if "asset_reference" not in data:
        return
    asset_id = identifier(data["asset_reference"], "asset", optional=True)
    if asset_id is None:
        row.asset_reference = None
        row.asset_snapshot = ""
        return
    asset = selectable_assets(user, row.site_id, restrict_site=True).filter(pk=asset_id).first()
    if asset is None:
        raise Problem("asset_unavailable", 409)
    row.asset_reference = asset.pk
    row.asset_snapshot = reference(asset)


def work_order_rows(user, filters):
    require(user, "workorders.view")
    rows = visible(WorkOrder.objects.select_related("maintenance").order_by("-id"), user, "workorders.view")
    if filters.get("review") == "mine":
        scopes = reviewer_scopes(user)
        runs = ApprovalRun.objects.filter(order__in=rows, outcome=ApprovalRun.Outcome.PENDING)
        pending = {run.order_id for run in runs.select_related("order", "template")
                   if can_review(user, run, scopes)}
        rows = rows.filter(pk__in=pending)
    return rows


@endpoint("GET", "POST", module="workorders")
def orders(request):
    if request.method == "GET":
        rows = work_order_rows(request.user, request.GET)
        if "page_size" in request.GET:
            from backbone.services.pagination import operational_page
            return {**operational_page(request,rows,serialize,['title', 'status', 'site__name'])}
        return {"items": [serialize(row) for row in rows]}
    data = body(request)
    fields = fields_from(data)
    require(request.user, "workorders.create", fields["site_id"])

    def create():
        from backbone.site_admin import require_active_site

        require_active_site(fields["site_id"], lock=True)
        row = WorkOrder(**fields)
        set_asset(request.user, row, data)
        row.save()
        audit(request.user, "workorder.create", row.pk)
        return serialize(row)

    payload = {**fields, "asset_reference": identifier(data.get("asset_reference"), "asset", optional=True)}
    return once(request, "workorder.create", payload, create)


def get_order(user, pk, *, lock=False):
    queryset = WorkOrder.objects.select_for_update() if lock else WorkOrder.objects.all()
    row = visible(queryset, user, "workorders.view").filter(pk=pk).first()
    if row is None:
        raise Problem("not_found", 404)
    return row


def check_revision(row, data):
    if identifier(data.get("revision"), "revision") != row.revision:
        raise Problem("revision_conflict", 409)
    if row.status != WorkOrder.Status.DRAFT:
        raise Problem("not_draft", 409)


@endpoint("GET", "PATCH", module="workorders")
def order(request, pk):
    if request.method == "GET":
        row = get_order(request.user, pk)
        return {**serialize(row), **review_details(request.user, row)}
    data = body(request)
    with transaction.atomic():
        row = get_order(request.user, pk, lock=True)
        require(request.user, "workorders.edit", row.site_id)
        check_revision(row, data)
        changes = fields_from(data)
        require(request.user, "workorders.edit", changes["site_id"])
        if hasattr(row, "maintenance") and (row.maintenance.stage != "waiting" or changes["site_id"] != row.site_id
                or data.get("asset_reference", row.asset_reference) != row.asset_reference):
            raise Problem("maintenance_asset_locked", 409)
        if changes["site_id"] != row.site_id and row.asset_reference and "asset_reference" not in data:
            raise Problem("review_asset_link", 409)
        for key, value in changes.items():
            setattr(row, key, value)
        set_asset(request.user, row, data)
        row.revision += 1
        row.save()
        audit(request.user, "workorder.edit", row.pk)
        return {**serialize(row), **review_details(request.user, row)}


@endpoint("POST", module="workorders")
def submit(request, pk):
    data = body(request)
    with transaction.atomic():
        row = get_order(request.user, pk, lock=True)
        require(request.user, "workorders.submit", row.site_id)
        check_revision(row, data)
        row.status = start_approval(request.user, row)
        row.submitted_at = timezone.now()
        row.revision += 1
        row.save(update_fields=["status", "submitted_at", "revision"])
        audit(request.user, "workorder.submit", row.pk)
        return {**serialize(row), **review_details(request.user, row)}


@endpoint("POST", module="workorders")
def review(request, pk):
    data = body(request)
    with transaction.atomic():
        row = get_order(request.user, pk, lock=True)
        apply_decision(request.user, row, data)
        return {**serialize(row), **review_details(request.user, row)}
