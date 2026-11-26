import re

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from accounting.views import day_field
from backbone.access import audit, has_permission, preload_scopes, visible
from backbone.http import Problem, body, endpoint, identifier, once, require, string
from backbone.services.pagination import paginate, positive_filter, search_rows
from backbone.site_admin import require_active_site
from purchasing.money import decimal_value
from sales.models import Customer
from sales.views import customer_values, quote
from . import services
from .models import Activity, CustomerRequest, Lead, Opportunity, QuotationLink, Stage


def site_input(data, user):
    site = identifier(data.get("site_id"), "site_id")
    require(user, "crm.view", site)
    require(user, "crm.manage", site)
    require_active_site(site, lock=True)
    return site


def lead_data(row):
    return {key: getattr(row, key) for key in ("id", "number", "name", "company", "email", "phone", "source",
            "state", "site_id", "owner_id", "revision")} | {"owner": row.owner.get_username(), "created_at": row.created_at.isoformat(),
            "opportunity_id": row.opportunity.pk if hasattr(row, "opportunity") else None}


def stage_data(row):
    return {key: getattr(row, key) for key in ("id", "code", "name_en", "name_ar", "order", "kind", "active", "revision")}


def opportunity_data(row, user):
    link = row.quotation_link if hasattr(row, "quotation_link") and has_permission(user, "sales.view", row.site_id) else None
    customer = row.customer if row.customer_id and has_permission(user, "sales.view", row.site_id) else None
    return {key: getattr(row, key) for key in ("id", "number", "title", "site_id", "owner_id", "lead_id",
            "stage_id", "currency", "revision")} | {
        "created_at": row.created_at.isoformat(), "expected_close": row.expected_close.isoformat() if row.expected_close else None,
        "customer_id": customer.pk if customer else None, "customer": customer.name if customer else None,
        "has_customer": row.customer_id is not None, "owner": row.owner.get_username(),
        "stage": stage_data(row.stage), "estimated_amount": format(row.estimated_amount, ".2f"),
        "quotation": {"id": link.document_id, "number": link.document.number, "status": link.document.status} if link else None,
    }


def opportunity_rows():
    return Opportunity.objects.select_related("stage", "owner", "customer", "quotation_link__document")


def common_filters(request, rows, fields):
    rows = visible(search_rows(rows, request.GET, fields), request.user, "crm.view")
    for filter_key in ("site", "owner"):
        if value := positive_filter(request.GET, filter_key):
            rows = rows.filter(**{filter_key + "_id": value})
    return rows.order_by("-pk")


def lead_values(data):
    values = {name: string(data, name, limit, required=name == "name") for name, limit in
              (("name", 120), ("company", 120), ("email", 254), ("phone", 48), ("source", 80))}
    if values["email"]:
        try:
            validate_email(values["email"])
        except ValidationError:
            raise Problem("invalid_email", field="email") from None
    return values


def opportunity_values(data):
    currency = string(data, "currency", 3).upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise Problem("invalid_currency", field="currency")
    return {"title": string(data, "title", 160), "currency": currency,
            "estimated_amount": decimal_value(data.get("estimated_amount"), 20, 2, "estimated_amount"),
            "expected_close": day_field(data, "expected_close") if data.get("expected_close") else None}


def editable(row):
    if row.stage.kind != "open":
        raise Problem("crm_closed_opportunity", 409)
    require_active_site(row.site_id, lock=True)


@endpoint("GET", module="crm")
def owner_choices(request):
    site = positive_filter(request.GET, "site")
    if site is None:
        raise Problem("invalid_site")
    require(request.user, "crm.view", site)
    require(request.user, "crm.manage", site)
    return {"items": list(services.owners(site).order_by("username").values("id", "username"))}


@endpoint("GET", "POST", module="crm")
def leads(request):
    user = request.user
    if request.method == "GET":
        require(user, "crm.view")
        rows = common_filters(request, Lead.objects.select_related("owner", "opportunity"), ("name", "company", "email"))
        state = request.GET.get("state")
        return paginate(request, rows.filter(state=state) if state else rows, lead_data)
    data = body(request)
    require(user, "crm.manage", identifier(data.get("site_id"), "site_id"))
    require(user, "crm.view", data["site_id"])

    def commit():
        site = site_input(data, user)
        row = Lead.objects.create(site_id=site, owner_id=services.owner_for(site, data.get("owner_id"), user.pk),
                                  **lead_values(data))
        audit(user, "crm.lead.create", row.number)
        return lead_data(row)

    return once(request, "crm.lead.create", data, commit)


@endpoint("GET", "PATCH", module="crm")
def lead(request, pk):
    user = request.user
    if request.method == "GET":
        return lead_data(services.scoped(Lead, user, pk))
    data = body(request)
    with transaction.atomic():
        row = services.scoped(Lead, user, pk, "crm.manage", lock=True)
        services.revision(row, data)
        require_active_site(row.site_id, lock=True)
        if row.state == "converted":
            raise Problem("crm_lead_converted", 409)
        state = data.get("state", row.state)
        if state not in ("new", "qualified", "disqualified"):
            raise Problem("invalid_state")
        old = (row.state, row.owner_id)
        for key, value in lead_values(data).items():
            setattr(row, key, value)
        row.owner_id = services.owner_for(row.site_id, data.get("owner_id"), row.owner_id)
        row.state = state
        row.revision += 1
        row.save()
        audit(user, "crm.lead.update", f"{row.number} · {old[0]} → {state} · owner #{old[1]} → #{row.owner_id}")
        return lead_data(row)


@endpoint("POST", module="crm")
def convert(request, pk):
    user, data = request.user, body(request)
    services.scoped(Lead, user, pk, "crm.manage")

    def commit():
        lead_site = Lead.objects.filter(pk=pk).values_list("site_id", flat=True).first()
        if lead_site is not None:
            require_active_site(lead_site, lock=True)
        row = services.scoped(Lead, user, pk, "crm.manage", lock=True)
        if row.state != "qualified":
            raise Problem("crm_lead_not_qualified", 409)
        services.revision(row, data)
        stage = Stage.objects.select_for_update().get(code="qualified")
        record = Opportunity.objects.create(site_id=row.site_id, owner_id=row.owner_id, lead=row,
                                            stage=stage, **opportunity_values(data))
        row.state = "converted"
        row.revision += 1
        row.save(update_fields=["state", "revision"])
        services.record_stage(user, record, stage)
        audit(user, "crm.lead.convert", f"{row.number} → {record.number}")
        return opportunity_data(record, user)

    return once(request, "crm.lead.convert", {"object_id": pk, "data": data}, commit)


@endpoint("GET", "POST", module="crm")
def opportunities(request):
    user = request.user
    if request.method == "GET":
        require(user, "crm.view")
        preload_scopes(user)
        rows = common_filters(request, opportunity_rows(), ("title", "lead__company", "lead__name"))
        if stage := positive_filter(request.GET, "stage"):
            rows = rows.filter(stage_id=stage)
        if status := request.GET.get("status"):
            if status not in ("open", "won", "lost"):
                raise Problem("invalid_status")
            rows = rows.filter(stage__kind=status)
        if currency := request.GET.get("currency"):
            if not re.fullmatch(r"[A-Z]{3}", currency):
                raise Problem("invalid_currency")
            rows = rows.filter(currency=currency)
        return paginate(request, rows, lambda row: opportunity_data(row, user))
    data = body(request)
    site = identifier(data.get("site_id"), "site_id")
    require(user, "crm.view", site)
    require(user, "crm.manage", site)
    require(user, "sales.view", site)
    services.require_module("customers")

    def commit():
        site_input(data, user)
        customer = customer_for(data)
        stage = Stage.objects.select_for_update().get(code="new")
        row = Opportunity.objects.create(site_id=site, customer=customer, stage=stage,
                                         owner_id=services.owner_for(site, data.get("owner_id"), user.pk),
                                         **opportunity_values(data))
        services.record_stage(user, row, stage)
        audit(user, "crm.opportunity.create", row.number)
        return opportunity_data(row, user)

    return once(request, "crm.opportunity.create", data, commit)


def customer_for(data):
    key = identifier(data.get("customer_id"), "customer_id")
    row = Customer.objects.select_for_update().filter(pk=key, active=True).first()
    if row is None:
        raise Problem("invalid_customer_id", field="customer_id", reason="invalid_choice")
    return row


@endpoint("GET", "PATCH", module="crm")
def opportunity(request, pk):
    user = request.user
    if request.method == "GET":
        return opportunity_data(services.scoped(opportunity_rows(), user, pk), user)
    data = body(request)
    with transaction.atomic():
        row = services.scoped(Opportunity, user, pk, "crm.manage", lock=True)
        services.revision(row, data)
        editable(row)
        previous_owner = row.owner_id
        values = opportunity_values(data)
        if hasattr(row, "quotation_link") and row.currency != values["currency"]:
            raise Problem("crm_quotation_currency_locked", 409)
        for key, value in values.items():
            setattr(row, key, value)
        row.owner_id = services.owner_for(row.site_id, data.get("owner_id"), row.owner_id)
        row.revision += 1
        row.save()
        audit(user, "crm.opportunity.update", f"{row.number} · owner #{previous_owner} → #{row.owner_id}")
        return opportunity_data(row, user)


@endpoint("POST", module="crm")
def transition(request, pk):
    user, data = request.user, body(request)
    with transaction.atomic():
        row = services.scoped(Opportunity, user, pk, "crm.manage", lock=True)
        services.revision(row, data)
        editable(row)
        stage = services.lookup(Stage.objects.select_for_update().filter(active=True), identifier(data.get("stage_id"), "stage_id"))
        if row.stage_id == stage.pk:
            return opportunity_data(row, user)
        reason = string(data, "reason", 500, required=stage.kind == "lost")
        if stage.kind == "won":
            if not QuotationLink.objects.select_related("document").filter(opportunity=row, document__status="accepted").exists():
                raise Problem("crm_accepted_quotation_required", 409)
            # Serialize acceptance/cancellation of the Sales quote during closure.
            from sales.models import Document
            document = Document.objects.select_for_update().get(crm_quotation_link__opportunity=row)
            if document.status != "accepted":
                raise Problem("crm_accepted_quotation_required", 409)
        if stage.kind in ("won", "lost") and row.customer_requests.filter(state="pending").exists():
            raise Problem("crm_customer_request_pending", 409)
        previous = row.stage
        row.stage = stage
        row.revision += 1
        row.save(update_fields=["stage", "revision"])
        services.record_stage(user, row, stage, previous, reason)
        return opportunity_data(row, user)


@endpoint("GET", module="crm")
def history(request, pk):
    row = services.scoped(Opportunity, request.user, pk)
    return paginate(request, row.history.select_related("actor").order_by("-pk"),
                    lambda step: {"id": step.pk, "previous": step.previous_names, "stage": step.stage_names,
                                  "actor": step.actor.get_username(), "reason": step.reason, "created_at": step.created_at})


@endpoint("GET", module="crm")
def customer_lookup(request):
    user = request.user
    site = positive_filter(request.GET, "site")
    if site is None:
        raise Problem("invalid_site")
    require(user, "crm.view", site)
    require(user, "sales.view", site)
    services.require_module("customers")
    rows = search_rows(Customer.objects.filter(active=True).order_by("code"), request.GET, ("code", "name"))
    return {"items": list(rows.values("id", "code", "name")[:40])}


def link_customer(user, row, customer):
    row.customer = customer
    row.revision += 1
    row.save(update_fields=["customer", "revision"])
    audit(user, "crm.customer.link", f"{row.number} · {customer.code}")


def check_new_customer(row):
    if row.customer_id:
        raise Problem("crm_customer_already_linked", 409)
    if row.customer_requests.filter(state="pending").exists():
        raise Problem("crm_customer_request_pending", 409)


@endpoint("POST", module="crm")
def customer_link(request, pk):
    user, data = request.user, body(request)
    services.require_module("customers")
    with transaction.atomic():
        row = services.scoped(Opportunity, user, pk, "crm.manage", lock=True)
        require(user, "sales.view", row.site_id)
        services.revision(row, data)
        editable(row)
        check_new_customer(row)
        link_customer(user, row, customer_for(data))
        return opportunity_data(row, user)


def require_customer_create(user, site):
    services.require_module("customers")
    require(user, "sales.view", site)
    require(user, "sales.customer", None)


@endpoint("POST", module="crm")
def customer_create(request, pk):
    user, data = request.user, body(request)
    current = services.scoped(Opportunity, user, pk, "crm.manage")
    require_customer_create(user, current.site_id)

    def commit():
        row = services.scoped(Opportunity, user, pk, "crm.manage", lock=True)
        services.revision(row, data)
        editable(row)
        check_new_customer(row)
        link_customer(user, row, services.create_customer(user, customer_values({**data, "active": True})))
        return opportunity_data(row, user)

    return once(request, "crm.customer.create", {"object_id": pk, "data": data}, commit)


def request_data(row):
    return {"id": row.pk, "number": row.number, "site_id": row.site_id, "opportunity_id": row.opportunity_id,
            "name": row.fields["name"], "state": row.state, "reason": row.reason, "revision": row.revision,
            "requested_by": row.requested_by.get_username(), "customer_id": row.customer_id,
            "customer_code": row.customer.code if row.customer_id else None, "fields": row.fields}


@endpoint("POST", module="crm")
def request_customer(request, pk):
    user, data = request.user, body(request)
    services.require_module("customers")
    services.scoped(Opportunity, user, pk, "crm.manage")

    def commit():
        row = services.scoped(Opportunity, user, pk, "crm.manage", lock=True)
        services.revision(row, data)
        editable(row)
        check_new_customer(row)
        pending = CustomerRequest.objects.create(site_id=row.site_id, opportunity=row, requested_by=user,
                                                fields=customer_values({**data, "active": True}))
        audit(user, "crm.customer.request", f"{row.number} · {pending.number}")
        return request_data(pending)

    return once(request, "crm.customer.request", {"object_id": pk, "data": data}, commit)


def customer_request_rows(user):
    owned = visible(CustomerRequest.objects.filter(requested_by=user), user, "crm.view")
    approved_scope = visible(visible(visible(CustomerRequest.objects.all(), user, "crm.view"), user, "crm.customer_approve"), user, "sales.view") \
        if has_permission(user, "sales.customer", None) else CustomerRequest.objects.none()
    return (owned | approved_scope).select_related("requested_by", "customer")


@endpoint("GET", module="crm")
def customer_requests(request, pk=None):
    require(request.user, "crm.view")
    rows = customer_request_rows(request.user)
    if pk is not None:
        row = services.lookup(rows, pk)
        return request_data(row) | {"can_decide": services.pending(request.user).filter(pk=pk).exists()}
    if opportunity_id := positive_filter(request.GET, "opportunity"):
        rows = rows.filter(opportunity_id=opportunity_id)
    return paginate(request, rows.order_by("-pk"), request_data)


@endpoint("POST", module="crm")
def decide_customer(request, pk):
    user, data = request.user, body(request)
    current = services.lookup(customer_request_rows(user), pk)
    require_customer_create(user, current.site_id)
    require(user, "crm.customer_approve", current.site_id)
    if current.requested_by_id == user.pk:
        raise Problem("crm_separate_approver", 403)
    action = data.get("action")
    if action not in ("approve", "reject"):
        raise Problem("invalid_action")
    with transaction.atomic():
        opportunity = services.scoped(Opportunity, user, current.opportunity_id, lock=True)
        row = CustomerRequest.objects.select_for_update().get(pk=pk)
        services.revision(row, data)
        editable(opportunity)
        if row.state != "pending" or opportunity.customer_id:
            raise Problem("crm_customer_request_closed", 409)
        row.reason = string(data, "reason", 500, required=action == "reject")
        if action == "approve":
            row.customer = services.create_customer(user, row.fields)
            link_customer(user, opportunity, row.customer)
        row.state = "approved" if action == "approve" else "rejected"
        row.decided_by = user
        row.decided_at = timezone.now()
        row.revision += 1
        row.save()
        audit(user, "crm.customer." + action, f"{opportunity.number} · {row.number}")
        return request_data(row) | {"can_decide": False}


class ExistingQuotation(Problem):
    def __init__(self, document):
        super().__init__("crm_quotation_exists", 409)
        self.document = document

    def response(self):
        return super().response() | {"quotation": {"id": self.document.pk, "number": self.document.number}}


@endpoint("POST", module="crm")
def quotation(request, pk):
    user, data = request.user, body(request)
    services.require_module("sales")
    services.require_module("customers")
    current = services.scoped(Opportunity, user, pk, "crm.manage")
    require(user, "sales.view", current.site_id)
    require(user, "sales.quote", current.site_id)

    def commit():
        row = services.scoped(Opportunity, user, pk, "crm.manage", lock=True)
        link = QuotationLink.objects.filter(opportunity=row).select_related("document").first()
        if link:
            raise ExistingQuotation(link.document)
        services.revision(row, data)
        editable(row)
        if not row.customer_id:
            raise Problem("crm_customer_required", 409)
        if row.currency != "AED":
            raise Problem("crm_sales_currency", field="currency")
        document = quote(user, {**data, "site_id": row.site_id, "customer_id": row.customer_id})
        QuotationLink.objects.create(opportunity=row, document=document, actor=user)
        row.revision += 1
        row.save(update_fields=["revision"])
        audit(user, "crm.quotation.create", f"{row.number} → {document.number}")
        return {"id": document.pk, "number": document.number, "status": document.status}

    return once(request, "crm.quotation.create", {"object_id": pk, "data": data}, commit)


def activity_data(row):
    return {key: getattr(row, key) for key in ("id", "number", "kind", "title", "notes", "site_id",
            "owner_id", "lead_id", "opportunity_id", "state", "revision")} | {
        "created_at": row.created_at.isoformat(), "due_at": row.due_at.isoformat() if row.due_at else None,
        "owner": row.owner.get_username(), "parent": row.lead.number if row.lead_id else row.opportunity.number,
    }


def activity_values(data):
    kind = data.get("kind")
    if kind not in ("meeting", "call", "task", "note"):
        raise Problem("invalid_kind", field="kind", reason="invalid_choice")
    raw = data.get("due_at")
    due_at = None
    if kind != "note":
        if not isinstance(raw, str) or not raw:
            raise Problem("invalid_due_at", field="due_at", reason="required")
        try:
            due_at = parse_datetime(raw)
            if due_at is None or timezone.is_naive(due_at):
                raise ValueError
        except (ValueError, OverflowError):
            raise Problem("invalid_due_at", field="due_at", reason="invalid_date") from None
    return {"kind": kind, "title": string(data, "title", 160),
            "notes": string(data, "notes", 1000, required=False), "due_at": due_at}


def activity_parent(user, data):
    lead_id, opportunity_id = data.get("lead_id"), data.get("opportunity_id")
    if (lead_id is None) == (opportunity_id is None):
        raise Problem("crm_activity_parent")
    if lead_id is not None:
        return services.scoped(Lead, user, identifier(lead_id, "lead_id"), "crm.manage"), None
    return None, services.scoped(Opportunity, user, identifier(opportunity_id, "opportunity_id"), "crm.manage")


@endpoint("GET", "POST", module="crm")
def activities(request):
    user = request.user
    if request.method == "GET":
        require(user, "crm.view")
        rows = common_filters(request, Activity.objects.select_related("owner", "lead", "opportunity"), ("title",))
        for key in ("lead", "opportunity"):
            if parent := positive_filter(request.GET, key):
                rows = rows.filter(**{key + "_id": parent})
        if state := request.GET.get("state"):
            rows = rows.filter(state=state)
        return paginate(request, rows, activity_data)
    data = body(request)
    lead, opportunity = activity_parent(user, data)

    def commit():
        site = (lead or opportunity).site_id
        require_active_site(site, lock=True)
        locked_lead = services.scoped(Lead, user, lead.pk, "crm.manage", lock=True) if lead else None
        locked_opp = services.scoped(Opportunity, user, opportunity.pk, "crm.manage", lock=True) if opportunity else None
        if locked_lead and locked_lead.state in ("converted", "disqualified"):
            raise Problem("crm_lead_closed", 409)
        if locked_opp and locked_opp.stage.kind != "open":
            raise Problem("crm_closed_opportunity", 409)
        row = Activity.objects.create(site_id=site, lead=locked_lead, opportunity=locked_opp,
                                      owner_id=services.owner_for(site, data.get("owner_id"), user.pk), **activity_values(data))
        audit(user, "crm.activity.create", row.number)
        return activity_data(row)

    return once(request, "crm.activity.create", data, commit)


@endpoint("GET", "PATCH", module="crm")
def activity(request, pk):
    user = request.user
    if request.method == "GET":
        return activity_data(services.scoped(Activity, user, pk))
    data = body(request)
    with transaction.atomic():
        row = services.scoped(Activity, user, pk, lock=True)
        services.revision(row, data)
        require_active_site(row.site_id, lock=True)
        if row.state != "open":
            raise Problem("crm_activity_closed", 409)
        action = data.get("action")
        if action == "complete":
            if row.owner_id != user.pk:
                require(user, "crm.manage", row.site_id)
            row.state = "completed"
        else:
            require(user, "crm.manage", row.site_id)
            if action == "cancel":
                row.state = "cancelled"
            elif action == "edit":
                values = activity_values(data)
                owner = services.owner_for(row.site_id, data.get("owner_id"), row.owner_id)
                if row.due_at != values["due_at"] or row.owner_id != owner:
                    row.schedule_version += 1
                for key, value in values.items():
                    setattr(row, key, value)
                row.owner_id = owner
            else:
                raise Problem("invalid_action")
        row.revision += 1
        row.save()
        audit(user, "crm.activity." + action, row.number)
        return activity_data(row)


def stage_fields(data):
    order = identifier(data.get("order"), "order")
    if order > 10_000:
        raise Problem("invalid_order", field="order")
    return {"name_en": string(data, "name_en", 80), "name_ar": string(data, "name_ar", 80), "order": order}


@endpoint("GET", "POST", module="crm")
def stages(request):
    user = request.user
    if request.method == "GET":
        require(user, "crm.view")
        rows = Stage.objects.all()
        if not has_permission(user, "crm.configure", None):
            rows = rows.filter(active=True)
        if "page_size" in request.GET:
            from backbone.services.pagination import operational_page
            return operational_page(request,rows.order_by("order","pk"),stage_data,["name_ar","name_en","code"])
        return {"items": [stage_data(row) for row in sorted(rows, key=lambda row: (("open", "won", "lost").index(row.kind), row.order, row.pk))]}
    require(user, "crm.configure", None)
    data = body(request)
    code = string(data, "code", 32).lower()
    if not re.fullmatch(r"[a-z][a-z0-9-]*", code):
        raise Problem("invalid_code", field="code")
    with transaction.atomic():
        row, created = Stage.objects.get_or_create(code=code, defaults=stage_fields(data))
        if not created:
            raise Problem("crm_duplicate_stage_code", 409, field="code", reason="duplicate")
        audit(user, "crm.stage.create", row.code)
        return stage_data(row), 201


@endpoint("PATCH", module="crm")
def stage_detail(request, pk):
    user, data = request.user, body(request)
    require(user, "crm.configure", None)
    with transaction.atomic():
        row = services.lookup(Stage.objects.select_for_update(), pk)
        services.revision(row, data)
        enabled = data.get("active")
        if type(enabled) is not bool:
            raise Problem("invalid_active")
        if not enabled and (row.kind != "open" or row.code in ("new", "qualified") or row.opportunity_set.exists()):
            raise Problem("crm_stage_in_use", 409)
        values = stage_fields(data)
        if row.kind != "open":
            values["order"] = row.order
        for key, value in values.items():
            setattr(row, key, value)
        row.active = enabled
        row.revision += 1
        row.save()
        audit(user, "crm.stage.update", row.code)
        return stage_data(row)
