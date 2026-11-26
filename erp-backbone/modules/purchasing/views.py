from backbone.validation import Validation
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from backbone.access import audit, has_permission, visible
from backbone.http import Problem, body, endpoint, identifier, once, require, site, string
from backbone.models import ModuleState, NumberingRule
from backbone.review_access import review_authority, reviewer_roles, reviewer_scopes
from backbone.services.inbox import approval_outcome, close_task, open_task
from backbone.services.documents import archive_rows, reject_archived
from backbone.services.numbering import allocate_number
from .models import PurchaseOrder, Requisition, Review, Template
from .money import CENT, check_total, decimal_value, line_amount

MODULE = "purchasing"
CURRENCIES = ("AED", "EGP", "EUR", "GBP", "SAR", "USD")


def currency(data):
    code = data.get("currency")
    if code not in CURRENCIES:
        raise Problem("invalid_currency")
    return code


def fields(data):
    checks = Validation()
    site_id = checks.read("site_id", site, data)
    if site_id is None and "site_id" not in checks.fields:
        checks.add("site_id", "invalid_site", ["required"])
    code = checks.read("currency", currency, data)
    entries = data.get("lines")
    if not isinstance(entries, list) or not 1 <= len(entries) <= 100:
        checks.add("lines", "invalid_lines", ["invalid"])
        entries = []
    lines = []
    total = Decimal("0.00")
    for index, entry in enumerate(entries):
        prefix = f"lines.{index}."
        if not isinstance(entry, dict):
            checks.add(f"lines.{index}", "invalid_lines", ["invalid"])
            continue
        quantity = checks.read(prefix + "quantity", decimal_value, entry.get("quantity"), 7, 3, "quantity")
        price = checks.read(prefix + "unit_price", decimal_value, entry.get("unit_price"), 9, 4, "unit_price")
        if quantity is not None and quantity <= 0:
            checks.add(prefix + "quantity", "invalid_quantity", ["min_value"])
        description = checks.read(prefix + "description", string, entry, "description", 200)
        if quantity is None or quantity <= 0 or price is None or description is None:
            continue
        amount = line_amount(quantity, price)
        total += amount
        lines.append({"description": description, "quantity": format(quantity, ".3f"),
                      "unit_price": format(price, ".4f"), "amount": str(amount)})
    title = checks.read("title", string, data, "title", 120)
    checks.finish()
    check_total(total)
    return {"title": title, "site_id": site_id, "currency": code, "lines": lines, "total": total}


def header(row):
    return {"id": row.pk, "number": row.document_number, "title": row.title, "site_id": row.site_id, "currency": row.currency,
            "total": str(row.total), "status": row.status, "revision": row.revision}


def authority(user, row, run, scopes):
    if run.status != "pending":
        return False, None
    return review_authority(user, run.submitter_id, run.steps[run.next_step]["id"], row.site_id, scopes)


def detail(user, row):
    runs = list(row.reviews.select_related("template", "submitter").order_by("-id"))
    can_review = bool(runs and row.status == Requisition.Status.IN_REVIEW
                      and authority(user, row, runs[0], reviewer_scopes(user, MODULE))[0])
    site_locked = bool(row.number and NumberingRule.objects.filter(kind="pr", per_site=True).exists())
    archived = archive_rows("pr").filter(object_id=row.pk).exists()
    linked = PurchaseOrder.objects.filter(source_pr=row).values("id", "number", "status").first()
    can_convert = (row.status == Requisition.Status.APPROVED and not archived and linked is None
                   and has_permission(user, "purchasing.po.create", row.site_id))
    return {**header(row), "site_locked": site_locked, "archived": archived, "lines": row.lines,
            "purchase_order": linked, "can_create_purchase_order": can_convert,
            "can_review": can_review, "reviews": [
        {"version": run.template.version, "currency": run.template.currency, "total": str(run.total),
         "threshold": str(run.template.threshold), "status": run.status,
         "steps": run.steps, "next_step": run.next_step, "decisions": run.decisions,
         "submitted_by": run.submitter.get_username(), "submitted_at": run.submitted_at.isoformat()}
        for run in runs
    ]}


def check_revision(data, row):
    if identifier(data.get("revision"), "revision") != row.revision:
        raise Problem("revision_conflict", 409)


def requisition_rows(user, filters):
    require(user, "purchasing.view")
    rows = visible(Requisition.objects.order_by("-id"), user, "purchasing.view")
    archived = archive_rows("pr").values("object_id")
    if filters.get("archived") == "1":
        rows = rows.filter(pk__in=archived)
    else:
        rows = rows.exclude(pk__in=archived)
    if filters.get("review") == "mine":
        scopes = reviewer_scopes(user, MODULE)
        pending = Review.objects.filter(requisition__in=rows, status="pending").select_related("requisition")
        ids = [run.requisition_id for run in pending if authority(user, run.requisition, run, scopes)[0]]
        rows = rows.filter(pk__in=ids)
    return rows


def issued_orders_for_planning(sites):
    from .models import PurchaseOrder

    return list(PurchaseOrder.objects.filter(site__in=sites,status="issued",currency="AED").order_by("-pk")
                .values("id","number","site_id","lines")[:500])


def create_draft(user, values):
    from backbone.site_admin import require_active_site

    require(user, "purchasing.create", values["site_id"])
    require(user, "purchasing.view", values["site_id"])
    require_active_site(values["site_id"], lock=True)
    number = allocate_number("pr", values["site_id"])
    row = Requisition.objects.create(number=number, **values)
    audit(user, "purchase.create", row.document_number)
    return row


@endpoint("GET", "POST", module=MODULE)
def requisitions(request):
    user = request.user
    if request.method == "GET":
        rows = requisition_rows(request.user, request.GET)
        if "page_size" in request.GET:
            from backbone.services.pagination import operational_page
            return {**operational_page(request,rows,lambda row:{**header(row),"archived":request.GET.get("archived")=="1"},['title', 'number', 'status']),"currencies":CURRENCIES}
        return {"items": [{**header(row), "archived": request.GET.get("archived") == "1"} for row in rows],
                "currencies": CURRENCIES}
    values = fields(body(request))
    require(user, "purchasing.create", values["site_id"])
    require(user, "purchasing.view", values["site_id"])

    def create():
        return detail(user, create_draft(user, values))

    response, status = once(request, "purchase.create", {**values, "total": str(values["total"])}, create)
    if "number" not in response:
        # Receipts from v0.5 retain the original response and legacy number.
        response = {**response, "number": f"PR-{response['id']}", "archived": False, "site_locked": False}
    return response, status


@endpoint("GET", "PATCH", module=MODULE)
def requisition(request, pk):
    user = request.user
    if request.method == "GET":
        return detail(user, find_row(user, pk))
    data = body(request)
    with transaction.atomic():
        from backbone.site_admin import require_active_site
        row = find_row(user, pk, lock=True)
        require_active_site(row.site_id, lock=True)
        reject_archived("pr", row)
        require(user, "purchasing.edit", row.site_id)
        check_revision(data, row)
        if row.status != Requisition.Status.DRAFT:
            raise Problem("not_draft", 409)
        values = fields(data)
        if row.number and values["site_id"] != row.site_id:
            if NumberingRule.objects.filter(kind="pr", per_site=True).exists():
                raise Problem("numbered_site_locked", 409)
        require(user, "purchasing.edit", values["site_id"])
        require(user, "purchasing.view", values["site_id"])
        for key, value in values.items():
            setattr(row, key, value)
        row.revision += 1
        row.save()
        audit(user, "purchase.edit", row.document_number)
        return detail(user, row)


def find_row(user, pk, *, lock=False):
    rows = Requisition.objects.select_for_update() if lock else Requisition.objects.all()
    row = visible(rows, user, "purchasing.view").filter(pk=pk).first()
    if row is None:
        raise Problem("not_found", 404)
    return row


@endpoint("POST", module=MODULE)
def submit(request, pk):
    data = body(request)
    user = request.user
    with transaction.atomic():
        from backbone.site_admin import require_active_site
        row = find_row(user, pk, lock=True)
        require_active_site(row.site_id, lock=True)
        reject_archived("pr", row)
        require(user, "purchasing.submit", row.site_id)
        check_revision(data, row)
        if row.status != Requisition.Status.DRAFT:
            raise Problem("not_draft", 409)
        if row.total <= 0:
            raise Problem("invalid_total")
        template = Template.objects.filter(currency=row.currency).order_by("-version").first()
        if template is None:
            raise Problem("purchase_template_missing", 409)
        steps = template.steps if row.total > template.threshold else template.steps[:1]
        run = Review.objects.create(requisition=row, template=template, submitter=user, steps=steps, total=row.total)
        open_task("pr", row.pk, f"{run.pk}:0", steps[0]["id"])
        row.status = Requisition.Status.IN_REVIEW
        row.revision += 1
        row.save(update_fields=["status", "revision"])
        audit(user, "purchase.submit", f"{row.document_number} / {row.currency} v{template.version}")
        return detail(user, row)


@endpoint("POST", module=MODULE)
def review(request, pk):
    data = body(request)
    user = request.user
    with transaction.atomic():
        from backbone.site_admin import require_active_site
        row = find_row(user, pk, lock=True)
        require_active_site(row.site_id, lock=True)
        reject_archived("pr", row)
        check_revision(data, row)
        if row.status != Requisition.Status.IN_REVIEW:
            raise Problem("not_in_review", 409)
        run = row.reviews.select_for_update(of=("self",)).select_related("template").get(status="pending")
        allowed, grant = authority(user, row, run, reviewer_scopes(user, MODULE))
        if not allowed:
            raise Problem("forbidden", 403)
        action = data.get("action")
        if action not in ("approve", "return"):
            raise Problem("invalid_approval_action")
        reason = string(data, "reason", 1000, required=action == "return")
        step = run.next_step
        close_task("pr", row.pk, f"{run.pk}:{step}")
        run.decisions.append({
            "step": run.next_step, "actor": user.get_username(), "action": action, "reason": reason,
            "decided_at": timezone.now().isoformat(), "delegation_id": grant.pk if grant else None,
        })
        if action == "return":
            run.status = "returned"
            row.status = Requisition.Status.DRAFT
        else:
            run.next_step += 1
            if run.next_step == len(run.steps):
                run.status = "approved"
                row.status = Requisition.Status.APPROVED
        run.save(update_fields=["decisions", "next_step", "status"])
        row.revision += 1
        row.save(update_fields=["status", "revision"])
        target = f"{row.document_number} / {row.currency} v{run.template.version}"
        if grant:
            target += f" / delegation #{grant.pk}"
        if run.status == "pending":
            open_task("pr", row.pk, f"{run.pk}:{run.next_step}", run.steps[run.next_step]["id"])
        else:
            approval_outcome(run.submitter_id, "pr", row.pk, run.pk, step, action)
        audit(user, f"purchase.approval.{action}", target)
        return detail(user, row)


def serialize_template(template):
    return {"currency": template.currency, "version": template.version, "threshold": str(template.threshold),
            "steps": template.steps}


@endpoint("GET", "POST", module=MODULE, admin=True)
def templates(request):
    roles = {role["id"]: role for role in reviewer_roles(MODULE)}
    if request.method == "GET":
        return {"roles": list(roles.values()), "currencies": CURRENCIES,
                "versions": [serialize_template(row) for row in Template.objects.order_by("currency", "-version")]}
    data = body(request)
    code = currency(data)
    threshold = decimal_value(data.get("threshold"), 15, 2, "threshold").quantize(CENT)
    manager = identifier(data.get("manager_role_id"), "approval_steps")
    finance = identifier(data.get("finance_role_id"), "approval_steps")
    if manager == finance or manager not in roles or finance not in roles:
        raise Problem("invalid_approval_steps")
    with transaction.atomic():
        ModuleState.objects.select_for_update().get(pk=MODULE)
        previous = Template.objects.filter(currency=code).order_by("-version").first()
        row = Template.objects.create(currency=code, version=previous.version + 1 if previous else 1,
                                      threshold=threshold, steps=[roles[manager], roles[finance]])
        audit(request.user, "purchase.approval.publish", f"{code} v{row.version}")
        return serialize_template(row), 201
