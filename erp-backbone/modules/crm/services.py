from django.contrib.auth import get_user_model
from django.db.models import Count, Q, Sum
from django.utils import timezone

from backbone.access import audit, has_permission, visible
from backbone.http import Problem, identifier, require
from backbone.models import Assignment, NoticeRead
from backbone.registry import enabled_modules
from sales.models import Customer
from .models import Activity, CustomerNumber, CustomerRequest, Opportunity, StageHistory


def require_module(module):
    if module not in enabled_modules():
        raise Problem("module_disabled", 404)


def lookup(model, pk):
    item = model.filter(pk=pk).first()
    if item is None:
        raise Problem("not_found", 404)
    return item


def scoped(model, user, pk, permission=None, *, lock=False):
    rows = visible(model.objects.all() if hasattr(model, "objects") else model, user, "crm.view")
    if lock:
        rows = rows.select_for_update()
    item = lookup(rows, pk)
    if permission:
        require(user, permission, item.site_id)
    return item


def revision(item, data):
    if identifier(data.get("revision"), "revision") != item.revision:
        raise Problem("revision_conflict", 409)


def owners(site):
    assigned = Assignment.objects.filter(Q(site_id=site) | Q(site__isnull=True), role__permissions__contains=["crm.view"])
    return get_user_model().objects.filter(is_active=True).filter(
        Q(is_superuser=True) | Q(pk__in=assigned.values("user_id")))


def owner_for(site, raw, fallback):
    if raw is None:
        return fallback
    key = identifier(raw, "owner_id")
    if not owners(site).filter(pk=key).exists():
        raise Problem("invalid_owner_id", field="owner_id", reason="invalid_choice")
    return key


def record_stage(user, opportunity, stage, previous=None, reason=""):
    labels = lambda row: {"en": row.name_en, "ar": row.name_ar} if row else {}
    StageHistory.objects.create(opportunity=opportunity, stage=stage, previous_stage=previous,
                                actor=user, reason=reason, stage_names=labels(stage), previous_names=labels(previous))
    audit(user, "crm.opportunity.stage", f"{opportunity.number} · {previous.code if previous else '—'} → {stage.code}")


def create_customer(user, fields):
    # Serialize allocations. Existing Sales customer codes remain valid, even
    # if a manual code already used one of the generated numbers.
    sequence = CustomerNumber.objects.select_for_update().get(pk=1)
    while True:
        number = sequence.next_number
        if number > 9_223_372_036_854_775_806:
            raise Problem("crm_customer_sequence_exhausted", 409)
        sequence.next_number += 1
        row, created = Customer.objects.get_or_create(code=f"CUS-{number:06d}", defaults=fields)
        if created:
            break
    sequence.save(update_fields=["next_number"])
    audit(user, "sales.customer", row.code)
    return row


def pending(user, site=None):
    if not has_permission(user, "sales.customer", None) or "customers" not in enabled_modules():
        return CustomerRequest.objects.none()
    rows = visible(visible(CustomerRequest.objects.filter(state="pending", site__active=True), user, "crm.view"),
                   user, "crm.customer_approve").exclude(requested_by=user)
    rows = visible(rows, user, "sales.view").filter(opportunity__stage__kind="open", opportunity__customer__isnull=True)
    return rows if site is None else rows.filter(site_id=site)


def due(user, site=None):
    rows = visible(Activity.objects.filter(owner=user, state="open", due_at__lte=timezone.now(),
                                           site__active=True), user, "crm.view")
    return rows if site is None else rows.filter(site_id=site)


def reminder_key(row):
    return f"crm:activity:{row.pk}:{row.schedule_version}"


def notice_rows(user, site=None):
    for row in due(user, site).order_by("-due_at", "-pk").iterator():
        yield {"key": reminder_key(row), "number": row.number, "route": f"/crm/activities/{row.pk}",
               "event": "crm_activity_due", "created_at": row.due_at.isoformat()}
    for row in pending(user, site).order_by("-pk").iterator():
        yield {"key": f"crm:customer:{row.pk}", "number": row.number, "route": f"/crm/customer-requests/{row.pk}",
               "event": "crm_customer_review", "created_at": row.created_at.isoformat()}


def unread_reminders(user):
    from django.db.models import CharField, Value
    from django.db.models.functions import Cast, Concat

    keys = NoticeRead.objects.filter(user=user, key__startswith="crm:").values("key")
    approvals = pending(user).annotate(notice_key=Concat(Value("crm:customer:"), Cast("pk", CharField())))
    activities = due(user).annotate(notice_key=Concat(Value("crm:activity:"), Cast("pk", CharField()),
                                                      Value(":"), Cast("schedule_version", CharField())))
    return activities.exclude(notice_key__in=keys).count() + approvals.exclude(notice_key__in=keys).count()


def dashboard(user, site):
    if not has_permission(user, "crm.view", site if site is not None else ...):
        return None
    rows = visible(Opportunity.objects.filter(stage__kind="open"), user, "crm.view")
    if site is not None:
        rows = rows.filter(site_id=site)
    grouped = list(rows.order_by().values("currency").annotate(total=Sum("estimated_amount"), count=Count("pk")))
    return {"count": sum(row["count"] for row in grouped),
            "currencies": {row["currency"]: format(row["total"], ".2f") for row in grouped},
            "filters": {"status": "open", **({"site": str(site)} if site is not None else {})}}
