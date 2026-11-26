from backbone.extensions import invoke_extension
import calendar
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Max, Sum
from django.utils import timezone

from accounting.models import Posting
from accounting.services import ledger_lock
from accounting.centers import center_choices, center_for_site
from accounting.views import day_field
from assets.models import Asset, AssetProfile
from assets.services import reference
from backbone.access import audit, has_permission, visible
from backbone.models import Site
from backbone.http import Problem, body, endpoint, identifier, once, require, string
from backbone.registry import enabled_modules
from inventory.models import Item, Location, Movement, SiteSettings
from inventory.stock import business_date, locked_site, prorate, record
from purchasing.money import decimal_value
from .approvals import start_approval
from .models import MaintenanceJob, MaintenancePlan, WorkOrder
from .views import get_order

PART_KINDS = ("maint_issue", "maint_return")
ZERO = Decimal("0.00")


def access(user, action="view", site=...):
    if not {"assets", "workorders"}.issubset(enabled_modules()):
        raise Problem("module_disabled", 404)
    require(user, "maintenance.view", site)
    require(user, "workorders.view", site)
    if action != "view":
        require(user, "maintenance." + action, site)


def asset_access(user, pk, action="view"):
    row = visible(Asset.objects.all(), user, "assets.view").filter(pk=pk, site__isnull=False).first()
    if not row:
        raise Problem("not_found", 404)
    access(user, action, row.site_id)
    return row


def revision(row, data):
    if row.revision != identifier(data.get("revision"), "revision"):
        raise Problem("revision_conflict", 409)


def parts_plan(data):
    entries = data.get("spare_parts", [])
    if not isinstance(entries, list) or len(entries) > 50:
        raise Problem("invalid_lines")
    parts = {}
    for row in entries:
        if not isinstance(row, dict):
            raise Problem("invalid_lines")
        key = identifier(row.get("item_id"), "item")
        qty = decimal_value(row.get("quantity"), 12, 3, "quantity")
        if key in parts or qty <= 0:
            raise Problem("invalid_quantity")
        parts[key] = {"item_id": key, "quantity": str(qty)}
    if Item.objects.filter(pk__in=parts, active=True, is_deleted=False).count() != len(parts):
        raise Problem("invalid_item")
    return list(parts.values())


def profile_data(asset, profile):
    return {"id": asset.pk, "tag": asset.tag, "name": asset.name, "site_id": asset.site_id,
            "equipment_type": profile.equipment_type, "functional_location": profile.functional_location,
            "state": profile.state, "parent_id": profile.parent_id, "spare_parts": profile.spare_parts,
            "revision": profile.revision if profile.pk else 0}


@endpoint("GET", "PATCH", module="workorders")
def asset_details(request, pk):
    user = request.user
    asset = asset_access(user, pk, "plan" if request.method == "PATCH" else "view")
    if request.method == "PATCH":
        with transaction.atomic():
            # Serialize hierarchy edits and generation for this asset's site.
            lock_asset_site(asset)
            profile = AssetProfile.objects.filter(asset=asset).first() or AssetProfile(asset=asset, revision=0)
            data = body(request)
            if type(data.get("revision")) is not int or data["revision"] != profile.revision:
                raise Problem("revision_conflict", 409)
            state = data.get("state")
            if state not in ("active", "out_of_service", "retired"):
                raise Problem("invalid_asset_state")
            if state != "active" and MaintenanceJob.objects.filter(asset=asset).exclude(stage__in=("closed", "cancelled")).exists():
                raise Problem("asset_has_open_maintenance", 409)
            parent = identifier(data.get("parent_id"), "parent", optional=True)
            if parent is not None and asset_access(user, parent).site_id != asset.site_id:
                raise Problem("asset_unavailable", 409)
            ancestor = parent
            while ancestor:
                if ancestor == asset.pk:
                    raise Problem("maintenance_parent_cycle", 409)
                ancestor = AssetProfile.objects.select_for_update().filter(asset_id=ancestor).values_list("parent_id", flat=True).first()
            profile.parent_id, profile.state = parent, state
            profile.equipment_type = string(data, "equipment_type", 100, required=False)
            profile.functional_location = string(data, "functional_location", 120, required=False)
            profile.spare_parts = parts_plan(data)
            profile.revision += 1
            profile.save()
            audit(user, "maintenance.asset.edit", asset.tag)
            return profile_data(asset, profile)
    profile = AssetProfile.objects.filter(asset=asset).first() or AssetProfile(asset=asset)
    plans = [plan_data(row) for row in MaintenancePlan.objects.filter(asset=asset).select_related("asset").order_by("pk")]
    historical = visible(visible(WorkOrder.objects.all(), user, "workorders.view"), user, "maintenance.view")
    orders = MaintenanceJob.objects.filter(asset=asset, order__in=historical).select_related("order").order_by("-pk")
    general = visible(WorkOrder.objects.filter(asset_reference=asset.pk,
                                               maintenance__isnull=True), user, "workorders.view").order_by("-pk")
    return {**profile_data(asset, profile), "plans": plans,
            "general_orders": [{"id": order.pk, "title": order.title, "status": order.status} for order in general],
            "orders": [{"id": job.order_id, "title": job.order.title, "stage": job.stage,
                        "completed_at": job.completed_at, "due_date": job.due_date, **costs(job.order_id)}
                       for job in orders]}


def due_on(plan, index):
    if plan.frequency != "months":
        return plan.first_due + timedelta(days=index * plan.interval * (7 if plan.frequency == "weeks" else 1))
    month = plan.first_due.year * 12 + plan.first_due.month - 1 + index * plan.interval
    year, offset = divmod(month, 12)
    if year > 9999:
        return None
    return date(year, offset + 1, min(plan.first_due.day, calendar.monthrange(year, offset + 1)[1]))


def next_due(plan):
    previous = MaintenanceJob.objects.filter(plan=plan).aggregate(last=Max("due_date"))["last"]
    if not previous or previous < plan.first_due:
        return plan.first_due
    if plan.frequency == "months":
        index = ((previous.year - plan.first_due.year) * 12 + previous.month - plan.first_due.month) // plan.interval
    else:
        index = (previous - plan.first_due).days // (plan.interval * (7 if plan.frequency == "weeks" else 1))
    try:
        day = due_on(plan, index)
        return due_on(plan, index + 1) if day and day <= previous else day
    except OverflowError:
        return None


def plan_data(plan):
    day = next_due(plan)
    today = business_date(SiteSettings.objects.filter(site_id=plan.asset.site_id).first(), timezone.now())
    return {"id": plan.pk, "title": plan.title, "asset_id": plan.asset_id, "asset": reference(plan.asset),
            "site_id": plan.asset.site_id, "first_due": plan.first_due.isoformat(), "next_due": day.isoformat() if day else None,
            "frequency": plan.frequency, "interval": plan.interval, "checklist": plan.checklist,
            "spare_parts": plan.spare_parts, "active": plan.active, "revision": plan.revision,
            "asset_retired": AssetProfile.objects.filter(asset_id=plan.asset_id, state="retired").exists(),
            "due": bool(plan.active and day and day <= today), "overdue": bool(plan.active and day and day < today),
            "previous_open": MaintenanceJob.objects.filter(plan=plan).exclude(stage__in=("closed", "cancelled")).exists()}


def plan_fields(data):
    interval = data.get("interval")
    frequency = data.get("frequency")
    active = data.get("active", True)
    if type(interval) is not int or not 1 <= interval <= 366 or frequency not in ("days", "weeks", "months") or type(active) is not bool:
        raise Problem("invalid_schedule")
    lines = data.get("checklist")
    if not isinstance(lines, list) or not 1 <= len(lines) <= 50:
        raise Problem("invalid_checklist")
    return {"title": string(data, "title", 120), "first_due": day_field(data, "first_due"), "interval": interval,
            "frequency": frequency, "checklist": [string({"step": line}, "step", 300) for line in lines],
            "spare_parts": parts_plan(data), "active": active}


@endpoint("GET", "POST", module="workorders")
def plans(request):
    user = request.user
    access(user)
    if request.method == "GET":
        assets = visible(Asset.objects.exclude(site=None), user, "assets.view")
        assets = visible(visible(assets, user, "maintenance.view"), user, "workorders.view")
        rows = MaintenancePlan.objects.filter(asset__in=assets).select_related("asset").order_by("pk")
        return {"items": [plan_data(row) for row in rows]}
    data = body(request)
    asset = asset_access(user, identifier(data.get("asset_id"), "asset"), "plan")

    def create():
        row = MaintenancePlan.objects.create(asset=asset, **plan_fields(data))
        audit(user, "maintenance.plan.create", row.pk)
        return plan_data(row)

    return once(request, "maintenance.plan.create", data, create)


def get_plan(user, pk):
    row = MaintenancePlan.objects.filter(pk=pk).first()
    if not row:
        raise Problem("not_found", 404)
    asset_access(user, row.asset_id, "plan")
    return row


@endpoint("PATCH", module="workorders")
def plan_edit(request, pk):
    user = request.user
    get_plan(user, pk)
    data = body(request)
    with transaction.atomic():
        row = MaintenancePlan.objects.select_for_update().get(pk=pk)
        revision(row, data)
        for key, value in plan_fields(data).items():
            setattr(row, key, value)
        row.revision += 1
        row.save()
        audit(user, "maintenance.plan.edit", row.pk)
        return plan_data(row)


def new_job(user, asset, title, description="", plan=None, due=None):
    require(user, "workorders.create", asset.site_id)
    row = WorkOrder.objects.create(title=title, description=description, site=asset.site,
                                  asset_reference=asset.pk, asset_snapshot=reference(asset))
    profile = AssetProfile.objects.filter(asset=asset).first()
    job = MaintenanceJob.objects.create(order=row, asset=asset, plan=plan, due_date=due, center_required=True,
        checklist=plan.checklist if plan else [], spare_parts=plan.spare_parts if plan else profile.spare_parts if profile else [])
    audit(user, "maintenance.workorder.create", row.pk)
    return job


def active_asset(asset):
    if AssetProfile.objects.filter(asset=asset, state="retired").exists():
        raise Problem("maintenance_asset_retired", 409)


@endpoint("POST", module="workorders")
def generate(request, pk):
    user = request.user
    get_plan(user, pk)
    data = body(request)
    day = day_field(data, "due_date")

    def create():
        plan = MaintenancePlan.objects.select_for_update().get(pk=pk)
        asset = asset_access(user, plan.asset_id, "plan")
        lock_asset_site(asset)
        existing = MaintenanceJob.objects.filter(plan=plan, due_date=day).first()
        if existing:
            return {"id": existing.order_id, "generated": False}
        active_asset(asset)
        today = business_date(SiteSettings.objects.filter(site_id=asset.site_id).first(), timezone.now())
        if not plan.active or day != next_due(plan) or day > today:
            raise Problem("maintenance_not_due", 409)
        if data.get("acknowledge_open") is not True and MaintenanceJob.objects.filter(plan=plan).exclude(
                stage__in=("closed", "cancelled")).exists():
            raise Problem("maintenance_previous_open", 409)
        require(user, "workorders.submit", asset.site_id)
        job = new_job(user, asset, plan.title, plan=plan, due=day)
        row = job.order
        row.status = start_approval(user, row)
        row.submitted_at = timezone.now()
        row.revision += 1
        row.save(update_fields=["status", "submitted_at", "revision"])
        return {"id": row.pk, "generated": True}

    return once(request, "maintenance.plan.generate", {"id": pk, **data}, create)


def lock_asset_site(asset):
    from backbone.site_admin import require_active_site

    require_active_site(asset.site_id, lock=True)
    if not Asset.objects.select_for_update().filter(pk=asset.pk, site_id=asset.site_id).exists():
        raise Problem("revision_conflict", 409)


@endpoint("POST", module="workorders")
def corrective(request):
    user = request.user
    data = body(request)
    asset = asset_access(user, identifier(data.get("asset_id"), "asset"), "plan")

    def create():
        lock_asset_site(asset)
        active_asset(asset)
        job = new_job(user, asset, string(data, "title", 120), string(data, "description", 5000, required=False))
        return {"id": job.order_id}

    return once(request, "maintenance.corrective", data, create)


def movements(order_id):
    return Movement.objects.filter(kind__in=PART_KINDS, facts__work_order_id=order_id)


def costs(order_id):
    totals = dict(movements(order_id).values("kind").annotate(total=Sum("value")).values_list("kind", "total"))
    issued, returned = -totals.get("maint_issue", ZERO), totals.get("maint_return", ZERO)
    return {"issued": format(issued, ".2f"), "returned": format(returned, ".2f"), "net": format(issued - returned, ".2f")}


def get_job(user, pk, lock=False):
    order = get_order(user, pk, lock=lock)
    access(user, site=order.site_id)
    job = MaintenanceJob.objects.filter(order=order).first()
    if not job:
        raise Problem("not_found", 404)
    return order, job


def movement_page_row(row):
    return {"id": row.pk, "kind": row.kind, "quantity": str(abs(row.quantity)), "value": str(abs(row.value)),
                     "item": row.item.name, "location": row.location.code, "original_id": row.original_id,
                     "posting": row.posting.state if hasattr(row, "posting") else "pending",
                     "entry_id": row.posting.entry_id if hasattr(row, "posting") else None}

@endpoint("GET", module="workorders")
def movement_page(request,pk):
    from backbone.services.pagination import paginate,search_rows
    get_job(request.user,pk)
    rows=movements(pk).select_related("item","location","posting").order_by("pk")
    return paginate(request,search_rows(rows,request.GET,["kind","item__name","location__code"]),movement_page_row)

@endpoint("GET", "POST", module="workorders")
def execution(request, pk):
    user = request.user
    order, job = get_job(user, pk)
    if request.method == "GET":
        catalog_users = []
        if has_permission(user, "maintenance.supervise", order.site_id):
            catalog_users = [{"id": candidate.pk, "name": candidate.get_username()}
                             for candidate in get_user_model().objects.filter(is_active=True).order_by("username")
                             if all(has_permission(candidate, p, order.site_id) for p in
                                    ("maintenance.execute", "maintenance.view", "workorders.view"))]
        item_names = dict(Item.objects.filter(pk__in=[part["item_id"] for part in job.spare_parts]).values_list("pk", "name"))
        return {"id": pk, "asset_id": job.asset_id, "asset": order.asset_snapshot, "status": order.status,
                "stage": job.stage, "revision": order.revision, "cost_center_id": job.cost_center_id,
                "center_required": job.center_required, "centers": center_choices(order.site_id), "plan_id": job.plan_id, "due_date": job.due_date,
                "checklist": job.checklist, "spare_parts": [{**part, "item": item_names.get(part["item_id"])} for part in job.spare_parts], "findings": job.findings,
                "results": job.results, "close_note": job.close_note, "assignee_id": job.assignee_id,
                "assignee": job.assignee.get_username() if job.assignee_id else "", "technicians": catalog_users,
                "is_assignee": job.assignee_id == user.pk, "started_at": job.started_at,
                "completed_at": job.completed_at, "closed_at": job.closed_at,
                "costs": costs(pk), "movements": [
                    movement_page_row(row)
                    for row in movements(pk).select_related("item", "location", "posting").order_by("pk")]}
    data = body(request)
    action = data.get("action")
    with transaction.atomic():
        order, job = get_job(user, pk, lock=True)
        revision(order, data)
        access(user, "execute" if action in ("start", "complete") else "supervise", order.site_id)
        if action == "cost_center" and job.stage in ("waiting", "in_progress") and not movements(pk).exists():
            job.cost_center = center_for_site(data.get("cost_center_id"), order.site_id, required=True)
        elif action == "authorize" and job.stage == "waiting" and order.status == "submitted":
            order.status = "approved"
        elif action == "assign" and job.stage == "waiting":
            candidate = get_user_model().objects.filter(pk=identifier(data.get("assignee_id"), "assignee"), is_active=True).first()
            if not candidate or not all(has_permission(candidate, p, order.site_id) for p in
                                         ("maintenance.execute", "maintenance.view", "workorders.view")):
                raise Problem("maintenance_invalid_assignee", 409)
            job.assignee = candidate
        elif action == "start" and job.stage == "waiting" and order.status == "approved" and job.assignee_id == user.pk:
            job.stage, job.started_at = "in_progress", timezone.now()
        elif action == "complete" and job.stage == "in_progress" and job.assignee_id == user.pk:
            results = data.get("results")
            if not isinstance(results, list) or len(results) != len(job.checklist):
                raise Problem("invalid_checklist")
            job.results = [string({"result": item}, "result", 300) for item in results]
            job.findings = string(data, "findings", 5000)
            job.stage, job.completed_at = "completed", timezone.now()
        elif action == "close" and job.stage == "completed":
            if movements(pk).exclude(posting__state__in=("posted", "zero")).exists():
                raise Problem("maintenance_pending_postings", 409)
            job.close_note = string(data, "reason", 1000, required=False)
            job.stage, job.closed_at = "closed", timezone.now()
        elif action == "cancel" and order.status != "in_review" and job.stage in ("waiting", "in_progress") and not movements(pk).exists():
            job.close_note = string(data, "reason", 1000)
            job.stage, job.closed_at = "cancelled", timezone.now()
        else:
            raise Problem("maintenance_transition_conflict", 409)
        if action in ("complete", "close", "cancel"):
            from hr.attendance import close_order_timers
            close_order_timers(pk, job.completed_at if action == "complete" else job.closed_at, user)
        job.save()
        order.revision += 1
        order.save(update_fields=["status", "revision"])
        audit(user, "maintenance.workorder." + action, f"WO-{pk} · {job.close_note}")
        return {"id": pk, "revision": order.revision, "stage": job.stage}


@endpoint("POST", module="workorders")
def parts(request, pk):
    user = request.user
    order, _ = get_job(user, pk)
    access(user, "parts", order.site_id)
    require(user, "inventory.view", order.site_id)
    if "inventory" not in enabled_modules():
        raise Problem("module_disabled", 404)
    data = body(request)
    qty = decimal_value(data.get("quantity"), 12, 3, "quantity")
    if qty <= 0:
        raise Problem("invalid_quantity")

    def create():
        order, job = get_job(user, pk, lock=True)
        settings = locked_site(order.site_id)
        if job.stage not in ("in_progress", "completed"):
            raise Problem("maintenance_transition_conflict", 409)
        original_id = identifier(data.get("original_id"), "movement", optional=True)
        original, value = None, ZERO
        if original_id:
            original = movements(pk).select_for_update().filter(pk=original_id, kind="maint_issue").first()
            if not original:
                raise Problem("not_found", 404)
            returned = original.reversals.aggregate(qty=Sum("quantity"), value=Sum("value"))
            left = -original.quantity - (returned["qty"] or ZERO)
            if qty > left:
                raise Problem("maintenance_return_exceeds_issue", 409)
            value = prorate(-original.value - (returned["value"] or ZERO), qty, left)
            kind, item, location = "maint_return", original.item, original.location
        else:
            if job.stage != "in_progress":
                raise Problem("maintenance_transition_conflict", 409)
            kind = "maint_issue"
            item = Item.objects.filter(pk=identifier(data.get("item_id"), "item"), active=True, is_deleted=False).first()
            location = Location.objects.filter(pk=identifier(data.get("location_id"), "location"), active=True,
                                               warehouse__site_id=order.site_id, warehouse__active=True).first()
            if not item or not location:
                raise Problem("maintenance_part_unavailable", 409)
        ledger = ledger_lock()
        if not ledger.activated_at or ledger.currency != "AED":
            raise Problem("maintenance_ledger_inactive", 409)
        if business_date(settings, timezone.now()) < ledger.cutoff:
            raise Problem("finance_before_cutoff", 409)
        if original:
            center_id = original.facts.get("cost_center_id")
        else:
            invoke_extension("project_work_issue",order.pk)
            center = center_for_site(job.cost_center_id, order.site_id, required=job.center_required)
            center_id = center.pk if center else None
        row = record(user, settings, item, location, kind, qty if original else -qty, value,
                     {"work_order_id": order.pk, "asset_id": job.asset_id, "cost_center_id": center_id}, original=original)
        audit(user, "maintenance.parts." + ("return" if original else "issue"), f"WO-{pk} / #{row.pk}")
        return {"id": row.pk, **costs(pk)}

    return once(request, "maintenance.parts", {"id": pk, **data}, create)
