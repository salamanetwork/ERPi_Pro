from itertools import islice

from django.db.models import Case, IntegerField, Q, Value, When
from django.views.decorators.cache import never_cache

from backbone.access import has_permission, visible
from backbone.http import Problem, body, endpoint, string
from backbone.models import ExperienceNotice, NoticeRead
from backbone.registry import enabled_modules
from .documents import archive_rows, definition
from .inbox import activity_limit, approval_tasks
from .report_filters import valid_filters


def result(kind, number, title, route):
    return {"kind": kind, "number": number, "title": title, "route": route}


def document_results(user, modules, query):
    from accounting.models import Entry
    from assets.models import Asset
    from inventory.models import Item
    from sales.models import Document
    from workorders.models import WorkOrder

    ident = query.rsplit("-", 1)[-1]
    numeric = int(ident) if ident.isascii() and ident.isdecimal() and len(ident) < 19 else None
    for kind in ("pr", "po", "grn"):
        spec = definition(kind)
        if spec.module not in modules:
            continue
        rows = visible(spec.model.objects.all(), user, spec.view_permission or spec.module + ".view")
        rows = rows.exclude(pk__in=archive_rows(kind).values("object_id"))
        title_key = "order__title" if kind == "grn" else "title"
        conditions = Q(number__icontains=query) | Q(**{title_key + "__icontains": query})
        if kind == "pr" and numeric is not None and query.lower().startswith("pr-"):
            conditions |= Q(pk=numeric, number__isnull=True)
        if kind == "po":
            conditions |= (Q(supplier__name__icontains=query) | Q(supplier__code__icontains=query)
                           | Q(snapshot__supplier__name__icontains=query) | Q(snapshot__supplier__code__icontains=query))
        if kind == "grn":
            conditions |= Q(order__number__icontains=query)
            rows = rows.select_related("order")
        rows = rows.filter(conditions).annotate(exact=Case(
            When(number__iexact=query, then=Value(0)), default=Value(1), output_field=IntegerField()))
        for row in rows.order_by("exact", "-id")[:25]:
            yield result(kind, row.document_number, row.order.title if kind == "grn" else row.title, f"{spec.route}/{row.pk}")

    if "workorders" in modules:
        conditions = Q(title__icontains=query) | Q(asset_snapshot__icontains=query)
        if numeric is not None and query.lower().startswith("wo-"):
            conditions |= Q(pk=numeric)
        for row in visible(WorkOrder.objects.filter(conditions), user, "workorders.view").order_by("-id")[:25]:
            yield result("wo", f"WO-{row.pk}", row.title, f"/work-orders/{row.pk}")

    if "assets" in modules:
        for row in visible(Asset.objects.filter(Q(tag__icontains=query) | Q(name__icontains=query)),
                           user, "assets.view").order_by("tag")[:25]:
            yield result("asset", row.tag, row.name, f"/assets?tag={row.pk}")

    if "sales" in modules:
        conditions = (Q(customer__name__icontains=query) | Q(customer__code__icontains=query)
                      | Q(snapshot__customer__name__icontains=query) | Q(snapshot__customer__code__icontains=query))
        for item_id in Item.objects.filter(code__icontains=query).order_by("code").values_list("id", flat=True)[:25]:
            conditions |= Q(lines__contains=[{"item_id": item_id}])
        prefixes = {"qt": "quote", "so": "order", "dn": "delivery", "si": "invoice",
                    "sr": "receipt", "rt": "return", "cn": "credit"}
        prefix = query.split("-", 1)[0].lower()
        if numeric is not None and prefix in prefixes:
            conditions |= Q(pk=numeric, kind=prefixes[prefix])
        rows = visible(Document.objects.filter(conditions).select_related("customer"), user, "sales.view")
        for row in rows.order_by("-id")[:25]:
            yield result("sales", row.number, row.snapshot.get("customer", {}).get("name", row.customer.name), f"/sales/{row.pk}")

    if "accounting" in modules and has_permission(user, "accounting.view", None):
        if numeric is not None and query.lower().startswith("je-"):
            for row in Entry.objects.filter(pk=numeric):
                yield result("journal", row.number, row.description, f"/accounting/journals?entry={row.pk}")

    if "billing_core" in modules:
        from billing_core.models import Invoice
        for row in visible(Invoice.objects.filter(number__icontains=query), user, "billing_core.view").order_by("-id")[:20]:
            yield result("invoice", row.number, f"{row.number} ({row.status})", "/billing")

    if "expenses" in modules:
        from expenses.views import rows_for
        exp_cond = Q(review_note__icontains=query)
        if numeric is not None and query.lower().startswith("exp-"):
            exp_cond |= Q(pk=numeric)
        for row in rows_for(user).filter(exp_cond).order_by("-id")[:20]:
            yield result("expense", f"EXP-{row.pk}", row.review_note or f"Expense #{row.pk}", "/expenses")

    if "subscriptions" in modules:
        from subscriptions.models import Contract
        sub_cond = (
            Q(version__plan__code__icontains=query)
            | Q(version__plan__name_en__icontains=query)
            | Q(version__plan__name_ar__icontains=query)
        )
        if numeric is not None and query.lower().startswith("sub-"):
            sub_cond |= Q(pk=numeric)
        for row in visible(Contract.objects.filter(sub_cond).select_related("version__plan"), user, "subscriptions.view").order_by("-id")[:20]:
            plan = row.version.plan
            yield result("subscription", f"SUB-{row.pk}", plan.name_en or plan.code, "/subscriptions")

    if "helpdesk" in modules:
        from helpdesk.access import tickets_for
        tck_cond = Q(subject__icontains=query)
        if numeric is not None and query.lower().startswith(("tck-", "tk-")):
            tck_cond |= Q(pk=numeric)
        for row in tickets_for(user).filter(tck_cond).order_by("-id")[:20]:
            yield result("ticket", f"TCK-{row.pk}", row.subject, "/helpdesk")

    if "crm" in modules:
        from crm.models import Lead
        lead_cond = Q(name__icontains=query) | Q(company__icontains=query) | Q(phone__icontains=query)
        if numeric is not None and query.lower().startswith("lead-"):
            lead_cond |= Q(pk=numeric)
        for row in visible(Lead.objects.filter(lead_cond), user, "crm.view").order_by("-id")[:20]:
            yield result("lead", f"LEAD-{row.pk}", row.name, "/crm")

def master_results(user, modules, query):
    from inventory.models import Item
    from purchasing.models import Supplier
    from sales.models import Customer

    for kind, module, model, path in (("customer", "customers", Customer, "/sales/customers"),
                                      ("supplier", "suppliers", Supplier, "/suppliers"),
                                      ("item", "inventory", Item, "/inventory/setup")):
        permitted = (has_permission(user, "suppliers.view", None) or has_permission(user, "suppliers.manage", None)
                     if kind == "supplier" else has_permission(user, "sales.view" if kind == "customer" else module + ".view"))
        if module not in modules or not permitted:
            continue
        rows = model.objects.filter(Q(code__icontains=query) | Q(name__icontains=query))
        if kind == "item":
            path = "/inventory/items"
        rows = rows.annotate(exact=Case(When(code__iexact=query, then=Value(0)), default=Value(1), output_field=IntegerField()))
        for row in rows.order_by("exact", "code")[:25]:
            yield result(kind, row.code, row.name, f"{path}?record={row.pk}")

    if "parties" in modules:
        from parties.models import Party
        pty_cond = Q(name_en__icontains=query) | Q(name_ar__icontains=query) | Q(identity_number__icontains=query)
        for row in visible(Party.objects.filter(pty_cond), user, "parties.view").order_by("id")[:20]:
            code = row.identity_number or f"PTY-{row.pk}"
            yield result("party", code, row.name_en or row.name_ar, "/parties")

    if "projects" in modules:
        from projects.access import accessible
        from projects.models import Project
        proj_cond = Q(code__icontains=query) | Q(name__icontains=query)
        proj_qs = Project.objects.all() if user.is_superuser else accessible(user).distinct()
        for row in proj_qs.filter(proj_cond).order_by("code")[:20]:
            yield result("project", row.code, row.name, "/projects")

    if "hr" in modules:
        from hr.directory import visible_employees
        emp_cond = Q(number__icontains=query) | Q(name__icontains=query)
        for row in visible_employees(user).filter(emp_cond).order_by("number")[:20]:
            yield result("employee", row.number, row.name, "/hr/employees")

@never_cache
@endpoint("GET")
def search(request):
    query = string(request.GET, "q", 120, required=False)
    if len(query) < 2:
        return {"items": []}
    enabled = enabled_modules()
    rows = [*document_results(request.user, enabled, query), *master_results(request.user, enabled, query)]
    term = query.casefold()
    return {"items": sorted(rows, key=lambda row: (row["number"].casefold() != term, row["kind"], row["number"]))[:80]}


def unread_notice_count(user, enabled, approval_keys):
    from django.db.models import CharField
    from django.db.models.functions import Cast, Concat
    from accounting.models import Posting
    from workorders.models import WorkOrder

    read = NoticeRead.objects.filter(user=user).values("key")
    approval_unread = len(approval_keys) - NoticeRead.objects.filter(user=user, key__in=approval_keys).count() if approval_keys else 0
    total = approval_unread
    notices = ExperienceNotice.objects.filter(user=user).exclude(key__in=read)
    if "helpdesk" in enabled:
        from helpdesk.access import tickets_for

        total += notices.filter(kind="helpdesk",object_id__in=tickets_for(user).values("pk")).count()
    if "marketing" in enabled:
        from marketing.feedback import accessible_notices
        total += accessible_notices(user).exclude(key__in=read).count()
    notices = notices.exclude(kind__in=["helpdesk","marketing_timezone","marketing_complaint"])
    for kind in notices.order_by().values_list("kind", flat=True).distinct():
        if kind == "wo":
            module, model, permission = "workorders", WorkOrder, "workorders.view"
        else:
            spec = definition(kind)
            module, model = spec.module, spec.model
            permission = spec.view_permission or module + ".view"
        if module in enabled:
            objects = visible(model.objects.all(), user, permission)
            objects = objects.exclude(pk__in=archive_rows(kind).values("object_id"))
            total += notices.filter(kind=kind, object_id__in=objects.order_by().values("pk")).count()
    if "accounting" in enabled and has_permission(user, "accounting.view", None) and has_permission(user, "accounting.post", None):
        pending = Posting.objects.filter(state="pending").annotate(
            notice_key=Concat(Value("posting:"), Cast("pk", CharField())))
        total += pending.exclude(notice_key__in=read).count()
    if "crm" in enabled and has_permission(user, "crm.view"):
        from crm.services import unread_reminders

        total += unread_reminders(user)
    return total


def notice_items(user):
    from accounting.models import Posting
    from workorders.models import WorkOrder

    enabled = enabled_modules()
    if "crm" in enabled and has_permission(user, "crm.view"):
        from crm.services import notice_rows

        yield from notice_rows(user)
    for item in approval_tasks(user, enabled):
        if item["action"] == "review":
            yield {"key": item["id"], "number": item["number"], "route": item["route"], "event": "review"}
    if "helpdesk" in enabled:
        yield from helpdesk_notices(user)
    if "marketing" in enabled:
        from marketing.feedback import notice_rows
        yield from notice_rows(user)
    for notice in ExperienceNotice.objects.filter(user=user).exclude(kind__in=["helpdesk","marketing_timezone","marketing_complaint"]).order_by("-id"):
        if notice.kind == "wo":
            module, model, path, permission = "workorders", WorkOrder, "/work-orders", "workorders.view"
        else:
            spec = definition(notice.kind)
            module, model, path = spec.module, spec.model, spec.route
            permission = spec.view_permission or module + ".view"
        if module not in enabled:
            continue
        row = visible(model.objects.filter(pk=notice.object_id), user, permission).first()
        if row is None or archive_rows(notice.kind).filter(object_id=row.pk).exists():
            continue
        yield {"key": notice.key, "number": f"WO-{row.pk}" if notice.kind == "wo" else row.document_number,
               "route": f"{path}/{row.pk}", "event": notice.event, "created_at": notice.created_at.isoformat()}
    if "accounting" in enabled and has_permission(user, "accounting.view", None) and has_permission(user, "accounting.post", None):
        for row in Posting.objects.filter(state="pending").select_related("sale__document").order_by("-id"):
            yield {"key": f"posting:{row.pk}", "number": row.sale.document.number if row.sale_id else f"#{row.movement_id}",
                   "route": "/accounting/postings", "event": "posting_pending"}


def helpdesk_notices(user, site=None, limit=100):
    from helpdesk.access import tickets_for

    rows = tickets_for(user)
    if site is not None:
        rows = rows.filter(site_id=site)
    for notice in ExperienceNotice.objects.filter(user=user,kind="helpdesk",object_id__in=rows.values("pk")).order_by("-pk")[:limit]:
        yield {"key":notice.key,"number":f"HD-{notice.object_id:06d}","route":f"/helpdesk/{notice.object_id}",
               "event":notice.event,"created_at":notice.created_at.isoformat()}


def recent_notices(user, site, limit):
    from workorders.models import WorkOrder

    notices = ExperienceNotice.objects.filter(user=user).exclude(kind__in=["helpdesk","marketing_timezone","marketing_complaint"])
    enabled = enabled_modules()
    eligible = Q(pk__in=[])
    sources = {}
    for kind in notices.order_by().values_list("kind", flat=True).distinct():
        if kind == "wo":
            module, model, path, permission = "workorders", WorkOrder, "/work-orders", "workorders.view"
        else:
            spec = definition(kind)
            module, model, path = spec.module, spec.model, spec.route
            permission = spec.view_permission or module + ".view"
        if module not in enabled:
            continue
        rows = visible(model.objects.all(), user, permission)
        if site is not None:
            rows = rows.filter(site_id=site)
        rows = rows.exclude(pk__in=archive_rows(kind).values("object_id"))
        eligible |= Q(kind=kind, object_id__in=rows.order_by().values("pk"))
        sources[kind] = (path, rows)
    selected = list(notices.filter(eligible).order_by("-created_at", "-pk")[:limit])
    documents = {}
    for kind in {notice.kind for notice in selected}:
        _, rows = sources[kind]
        documents[kind] = rows.in_bulk([notice.object_id for notice in selected if notice.kind == kind])
    result = [{"key": notice.key, "number": f"WO-{row.pk}" if notice.kind == "wo" else row.document_number,
             "route": f"{sources[notice.kind][0]}/{row.pk}", "event": notice.event, "created_at": notice.created_at.isoformat()}
            for notice in selected if (row := documents[notice.kind].get(notice.object_id)) is not None]
    if "crm" in enabled and has_permission(user, "crm.view"):
        from crm.services import notice_rows

        result.extend(islice(notice_rows(user, site), limit))
    if "helpdesk" in enabled:
        result.extend(helpdesk_notices(user,site,limit))
    if "marketing" in enabled:
        from marketing.feedback import notice_rows
        result.extend(notice_rows(user,site,limit))
    result.sort(key=lambda row: row["created_at"], reverse=True)
    return result[:limit]


@never_cache
@endpoint("GET", "POST")
def notifications(request):
    user = request.user
    if request.method == "GET" and (limit := activity_limit(request.GET)) is not None:
        filters = valid_filters("finance", {"site": request.GET.get("site", "")})
        return {"items": recent_notices(user, int(filters["site"]) if "site" in filters else None, limit)}
    rows = list(notice_items(user))
    if request.method == "POST":
        data = body(request)
        if data.get("all") is True:
            keys = [row["key"] for row in rows]
        else:
            key = string(data, "key", 120)
            if not any(row["key"] == key for row in rows):
                raise Problem("not_found", 404)
            keys = [key]
        NoticeRead.objects.bulk_create([NoticeRead(user=user, key=key) for key in keys], ignore_conflicts=True)
    read = set(NoticeRead.objects.filter(user=user, key__in=[row["key"] for row in rows]).values_list("key", flat=True))
    for row in rows:
        row["read"] = row["key"] in read
    # Unread first so older outstanding notices stay reachable.
    rows.sort(key=lambda row: row["read"])
    if "page_size" in request.GET:
        from .pagination import paginate_items
        return {**paginate_items(request,rows,("number","event")), "unread":sum(not r["read"] for r in rows)}
    return {"items": rows[:100], "unread": sum(not row["read"] for row in rows)}
