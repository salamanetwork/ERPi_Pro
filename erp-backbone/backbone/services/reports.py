from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.db import connection
from django.db.models import Case, CharField, DecimalField, Exists, F, OuterRef, Q, Subquery, Sum, Value, When
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.views.decorators.cache import never_cache

from accounting.models import Account, Accrual, InvoiceMatch, Line, Posting, SettlementAllocation, SupplierInvoice
from assets.models import Asset, AssetProfile
from backbone.access import has_permission, scopes
from backbone.http import Problem, endpoint, require
from backbone.registry import enabled_modules
from inventory.models import Item, Movement, OrderLine, Position, SiteSettings, Valuation
from inventory.stock import business_date
from purchasing.models import PurchaseOrder, Supplier
from sales.models import Customer, Document
from workorders.maintenance import due_on, next_due
from workorders.models import MaintenanceJob, MaintenancePlan, WorkOrder
from .csv_export import csv_response
from .report_filters import AGING, AS_OF, BOARDS, METRICS, RANGE, RELEVANT, valid_filters
from .documents import archive_rows

ZERO = Decimal("0.00")
METRIC_ACCESS = {
    "ap-aging": ("accounting", "ap.view"), "ar-aging": ("sales", "sales.view"),
    "grni": ("accounting", "accounting.view"), "pending-postings": ("accounting", "accounting.view"),
    "open-purchases": ("purchasing", "purchasing.view"), "overdue-purchases": ("purchasing", "purchasing.view"),
    "undated-purchases": ("purchasing", "purchasing.view"), "stock-value": ("inventory", "inventory.view"),
    **{metric: ("sales", "sales.view") for metric in BOARDS["sales"]},
    **{metric: ("workorders", "maintenance.view") for metric in BOARDS["maintenance"]},
}
COLUMNS = ("reference", "description", "site_id", "party", "date", "due_date", "status", "quantity", "amount",
           "currency", "account_id", "issued", "returned", "on_time", "order", "locations", "path")
MONEY = {"ap-aging", "ar-aging", "grni", "stock-value", "deliveries", "sales-invoices", "net-posted-sales", "collections",
         "unbilled-sales", "maintenance-parts", "maintenance-posted"}


def available(user, metric, enabled):
    module, permission = METRIC_ACCESS[metric]
    if module not in enabled:
        return False
    if metric == "ar-aging" and "accounting" not in enabled:
        return False
    if module == "workorders":
        return "assets" in enabled and has_permission(user, permission) and has_permission(user, "workorders.view")
    return has_permission(user, permission, None) if permission == "accounting.view" else has_permission(user, permission)


def scoped(rows, user, permission, filters, field="site_id", extra=()):
    for key in (permission, *extra):
        allowed = scopes(user, key)
        if None not in allowed:
            rows = rows.filter(**{field + "__in": allowed})
    if "site" in filters:
        rows = rows.filter(**{field: int(filters["site"])})
    return rows


def party_filter(rows, filters, key, field=None):
    return rows.filter(**{field or key + "_id": int(filters[key])}) if key in filters else rows


def span(rows, filters, field):
    return rows.filter(**{field + "__range": (filters["from"], filters["to"])})


def bucket(day, due, amount):
    elapsed = (day - due).days if due else 0
    return ("credit" if amount < 0 else "current" if elapsed <= 0 else "1-30" if elapsed <= 30
            else "31-60" if elapsed <= 60 else "61-90" if elapsed <= 90 else "90+")


def ap_exposure(user, filters, extra=()):
    day = date.fromisoformat(filters["as_of"])
    rows = scoped(SupplierInvoice.objects.filter(entry__date__lte=day).filter(
        Q(reversal_entry__isnull=True) | Q(reversal_entry__date__gt=day)), user, "ap.view", filters, extra=extra)
    rows = party_filter(rows, filters, "supplier")
    lines = Line.objects.filter(entry_id=OuterRef("entry_id"), amount__lt=0, account__code="AP-CONTROL")
    if "account" in filters:
        lines = lines.filter(account_id=filters["account"])
    rows = rows.filter(Exists(lines))
    if "currency" in filters:
        rows = rows.filter(entry__ledger__currency=filters["currency"])
    settlements = SettlementAllocation.objects.filter(invoice_id=OuterRef("pk"), settlement__entry__date__lte=day)
    payments = settlements.order_by().values("invoice_id").annotate(total=Sum("amount")).values("total")
    precision = DecimalField(max_digits=26, decimal_places=2)
    return rows.annotate(open_amount=F("total") - Coalesce(Subquery(payments), Value(ZERO, output_field=precision)),
                         control_account=Subquery(lines.order_by("-account_id").values("account_id")[:1]))


def ap_rows(user, filters, extra):
    day = date.fromisoformat(filters["as_of"])
    rows = ap_exposure(user, filters, extra).exclude(open_amount=0).select_related("supplier", "entry__ledger")
    for row in rows.order_by("due_date", "pk").iterator():
        yield dict(reference=row.document_number, description=row.supplier_number, site_id=row.site_id,
                   party=row.snapshot.get("supplier", row.supplier.name), date=row.entry.date,
                   due_date=row.due_date, status=bucket(day, row.due_date, row.open_amount), amount=row.open_amount,
                   account_id=row.control_account, currency=row.entry.ledger.currency, path=f"/payables/invoices/{row.pk}")


def ap_buckets(user, filters):
    day = date.fromisoformat(filters["as_of"])
    def threshold(days):
        return date.fromordinal(max(1, day.toordinal() - days))
    return (ap_exposure(user, filters).exclude(open_amount=0).order_by().annotate(
        group=Case(When(open_amount__lt=0, then=Value("credit")),
                   When(due_date__gte=day, then=Value("current")),
                   When(due_date__gte=threshold(30), then=Value("1-30")),
                   When(due_date__gte=threshold(60), then=Value("31-60")),
                   When(due_date__gte=threshold(90), then=Value("61-90")),
                   default=Value("90+"), output_field=CharField()),
        currency=F("entry__ledger__currency"))
        .values("currency", "group").annotate(amount=Sum("open_amount")))


def ar_documents(user, filters, extra):
    day = date.fromisoformat(filters["as_of"])
    docs = scoped(Document.objects.filter(kind__in=("invoice", "receipt", "credit"),
        postingevent__posting__entry__date__lte=day, postingevent__posting__state="posted"),
        user, "sales.view", filters, extra=extra)
    docs = party_filter(docs, filters, "customer")
    invoices = docs.filter(kind="invoice")
    if "account" in filters:
        invoices = invoices.filter(postingevent__posting__entry__rule__offset_id=filters["account"])
    if "currency" in filters:
        invoices = invoices.filter(postingevent__posting__entry__ledger__currency=filters["currency"])
    return docs, invoices


def ar_settled(docs):
    # Receipt allocations are stored as JSON lines and may span several invoices.
    # Group the already-scoped documents in PostgreSQL before reading the results.
    sql, parameters = docs.exclude(kind="invoice").order_by().values("kind", "source_id", "amount", "lines").query.sql_with_params()
    with connection.cursor() as cursor:
        cursor.execute(f"""
            WITH documents(kind, source_id, amount, lines) AS ({sql}),
            allocations AS (
                SELECT source_id AS invoice_id, amount FROM documents WHERE kind = 'credit'
                UNION ALL
                SELECT (allocation->>'invoice_id')::bigint, (allocation->>'amount')::numeric
                FROM documents
                CROSS JOIN LATERAL jsonb_array_elements(documents.lines) AS allocation
                WHERE kind = 'receipt'
            )
            SELECT invoice_id, SUM(amount) FROM allocations GROUP BY invoice_id
        """, parameters)
        return dict(cursor.fetchall())


def ar_rows(user, filters, extra):
    day = date.fromisoformat(filters["as_of"])
    docs, invoices = ar_documents(user, filters, extra)
    settled = ar_settled(docs)
    invoices = invoices.select_related("customer", "postingevent__posting__entry__rule",
                                       "postingevent__posting__entry__ledger").defer("lines")
    for doc in invoices.order_by("due", "pk").iterator():
        amount = doc.amount - settled.get(doc.pk, ZERO)
        if amount:
            entry = doc.postingevent.posting.entry
            yield dict(reference=doc.number, site_id=doc.site_id, party=doc.snapshot.get("customer", {}).get("name", doc.customer.name),
                       date=entry.date, due_date=doc.due, status=bucket(day, doc.due, amount), amount=amount,
                       account_id=entry.rule.offset_id if entry.rule_id else None, currency=entry.ledger.currency, path=f"/sales/{doc.pk}")


def grni_rows(user, filters, extra):
    day = date.fromisoformat(filters["as_of"])
    accruals = scoped(Accrual.objects.filter(Q(entry__date__lte=day) | Q(entry__isnull=True)),
                      user, "accounting.view", filters, "receipt_movement__site_id", extra)
    accruals = party_filter(accruals, filters, "supplier", "receipt_movement__receipt__order__supplier_id")
    accruals = party_filter(accruals, filters, "account")
    invoices = dict(InvoiceMatch.objects.filter(accrual__in=accruals, invoice__entry__date__lte=day)
                    .filter(Q(invoice__reversal_entry__isnull=True) | Q(invoice__reversal_entry__date__gt=day))
                    .values("accrual_id").annotate(value=Sum("amount")).values_list("accrual_id", "value"))
    returns = defaultdict(lambda: ZERO)
    reversed_moves = Movement.objects.filter(original__accrual__in=accruals).filter(
        Q(posting__state="opening", business_date__lte=day) | Q(posting__entry__date__lte=day, posting__state="posted")
        | Q(posting__state="zero", business_date__lte=day))
    for original, facts in reversed_moves.values_list("original_id", "facts").iterator():
        returns[original] += Decimal(facts["original_receipt_value"])
    for row in accruals.select_related("ledger", "entry", "receipt_movement__receipt__order").order_by("pk").iterator():
        move = row.receipt_movement
        if not row.entry_id and (move.business_date > day or row.ledger.cutoff and row.ledger.cutoff > day):
            continue
        amount = move.value - returns[move.pk] - invoices.get(row.pk, ZERO)
        if amount:
            receipt = move.receipt
            order = receipt.order if receipt else None
            yield dict(reference=receipt.number if receipt else f"#{move.pk}", description=f"#{move.pk}",
                       site_id=move.site_id, party=order.snapshot["supplier"]["name"] if order else "",
                       order=order.number if order else "", date=row.entry.date if row.entry_id else move.business_date,
                       amount=amount, account_id=row.account_id, currency=row.ledger.currency,
                       path=f"/inventory/receipts/{receipt.pk}" if receipt else "")


def pending_rows(user, filters, extra):
    rows = Posting.objects.filter(state="pending").select_related("movement", "sale__document")
    targets = None
    if "site" in filters:
        targets = {int(filters["site"])}
    for key in extra:
        allowed = scopes(user, key)
        if None not in allowed:
            targets = allowed if targets is None else targets & allowed
    if targets is not None:
        rows = rows.filter(Q(movement__site_id__in=targets) | Q(sale__document__site_id__in=targets))
    for row in rows.order_by("pk").iterator():
        doc = row.sale.document if row.sale_id else None
        move = row.movement
        site_id = doc.site_id if doc else move.site_id
        day = doc.date if doc else move.business_date
        yield dict(reference=doc.number if doc else f"#{move.pk}", site_id=site_id, date=day,
                   status=row.error or "pending", path="/accounting/postings")


def purchase_rows(user, filters, extra, metric):
    now = timezone.now()
    settings = SiteSettings.objects.in_bulk(field_name="site_id")
    rows = scoped(PurchaseOrder.objects.filter(status="issued").exclude(pk__in=archive_rows("po").values("object_id")),
                  user, "purchasing.view", filters, extra=extra)
    rows = party_filter(rows, filters, "supplier")
    quantities = {(order, key): qty for order, key, qty in OrderLine.objects.filter(order__in=rows)
                  .values_list("order_id", "line_id", "received").iterator()}
    for order in rows.order_by("pk").iterator():
        today = business_date(settings.get(order.site_id), now).isoformat()
        snapshot = order.snapshot or {}
        for line in snapshot.get("lines", []):
            qty = Decimal(line["quantity"]) - quantities.get((order.pk, line["id"]), ZERO)
            due = line.get("delivery_due")
            if qty <= 0 or (metric == "overdue-purchases" and (not due or due >= today)) or (metric == "undated-purchases" and due):
                continue
            yield dict(reference=order.number, description=line["description"], site_id=order.site_id,
                       party=snapshot["supplier"]["name"], due_date=due, quantity=qty, order=order.number,
                       status="undated" if not due else "overdue" if due < today else "open",
                       path=f"/purchase-orders/{order.pk}")


def stock_rows(user, filters, extra):
    rows = scoped(Valuation.objects.filter(quantity__gt=0).select_related("item"), user, "inventory.view", filters, extra=extra)
    rows = party_filter(rows, filters, "item")
    locations = defaultdict(list)
    positions = scoped(Position.objects.filter(quantity__gt=0), user, "inventory.view", filters,
                       "location__warehouse__site_id", extra)
    positions = party_filter(positions, filters, "item")
    for site, item, warehouse, location, quantity in positions.order_by("location__warehouse__code", "location__code").values_list(
            "site_id", "item_id", "location__warehouse__code", "location__code", "quantity").iterator():
        locations[site, item].append(f"{warehouse}/{location}: {quantity:f}")
    for row in rows.order_by("site_id", "item__code").iterator():
        yield dict(reference=row.item.code, description=row.item.name, site_id=row.site_id,
                   quantity=row.quantity, amount=row.value, locations="; ".join(locations[row.site_id, row.item_id]),
                   path="/inventory/balances")


def posted_sales(user, filters, extra=()):
    rows = Document.objects.filter(kind__in=("invoice", "credit"), postingevent__posting__state="posted")
    rows = scoped(rows, user, "sales.view", filters, extra=extra)
    rows = party_filter(rows, filters, "customer")
    if "currency" in filters:
        rows = rows.filter(postingevent__posting__entry__ledger__currency=filters["currency"])
    return span(rows, filters, "postingevent__posting__entry__date")


def net_posted_sales(user, filters, extra):
    rows = posted_sales(user, filters, extra).select_related("customer", "postingevent__posting__entry__ledger")
    for doc in rows.order_by("postingevent__posting__entry__date", "pk").iterator():
        entry = doc.postingevent.posting.entry
        yield dict(reference=doc.number, site_id=doc.site_id, party=doc.customer.name,
                   date=entry.date, status=doc.kind, currency=entry.ledger.currency,
                   amount=-doc.amount if doc.kind == "credit" else doc.amount, path=f"/sales/{doc.pk}")


def collections(user, filters, extra=()):
    # Legacy receipts may predate the accounting integration.
    rows = scoped(Document.objects.filter(kind="receipt"), user, "sales.view", filters, extra=extra)
    rows = party_filter(rows, filters, "customer")
    rows = span(rows, filters, "date").annotate(report_currency=Coalesce(
        "postingevent__posting__entry__ledger__currency", Value("AED"), output_field=CharField()))
    return rows.filter(report_currency=filters["currency"]) if "currency" in filters else rows


def sales_rows(user, filters, extra, metric):
    kind = {"deliveries": "delivery", "sales-invoices": "invoice", "collections": "receipt", "unbilled-sales": "delivery"}[metric]
    if metric == "collections":
        for doc in collections(user, filters, extra).select_related("customer").order_by("date", "pk").iterator():
            yield dict(reference=doc.number, site_id=doc.site_id, party=doc.snapshot.get("customer", {}).get("name", doc.customer.name),
                       date=doc.date, status=doc.status, amount=doc.amount, currency=doc.report_currency, path=f"/sales/{doc.pk}")
        return
    rows = scoped(Document.objects.filter(kind=kind).select_related("customer"), user, "sales.view", filters, extra=extra)
    rows = party_filter(rows, filters, "customer")
    allocated = defaultdict(lambda: ZERO)
    if metric in RANGE:
        rows = span(rows, filters, "date")
    else:
        invoices = Document.objects.filter(source__in=rows, kind="invoice")
        for key, total in invoices.values("source_id").annotate(total=Sum("amount")).values_list("source_id", "total"):
            allocated[key] += total
        returns = Document.objects.filter(source__in=rows, kind="return", snapshot__credited=False)
        for key, total in returns.values("source_id").annotate(total=Sum("amount")).values_list("source_id", "total"):
            allocated[key] += total
    for doc in rows.order_by("date", "pk").iterator():
        amount = doc.amount - allocated[doc.pk]
        if amount or metric != "unbilled-sales":
            yield dict(reference=doc.number, site_id=doc.site_id, party=doc.snapshot.get("customer", {}).get("name", doc.customer.name),
                       date=doc.date, status=doc.status, amount=amount, path=f"/sales/{doc.pk}")


def maintenance_rows(user, filters, extra, metric):
    assets = scoped(Asset.objects.all(), user, "maintenance.view", filters, extra=("workorders.view", *extra))
    assets = party_filter(assets, filters, "asset", "pk")
    historical = scoped(WorkOrder.objects.all(), user, "maintenance.view", filters, extra=("workorders.view", *extra))
    jobs = MaintenanceJob.objects.filter(order__in=historical).select_related("order", "asset")
    jobs = party_filter(jobs, filters, "asset", "asset_id")
    if metric in ("maintenance-parts", "maintenance-posted"):
        moves = scoped(Movement.objects.filter(kind__in=("maint_issue", "maint_return"), item__isnull=False),
                       user, "maintenance.view", filters, extra=("workorders.view", *extra))
        if "asset" in filters:
            moves = moves.filter(facts__asset_id=int(filters["asset"]))
        if metric == "maintenance-posted":
            moves = span(moves.filter(posting__state="posted"), filters, "posting__entry__date")
        else:
            moves = span(moves, filters, "business_date")
        orders = {job.order_id: job for job in jobs}
        for move in moves.select_related("posting__entry", "item").order_by("pk").iterator():
            job = orders.get(move.facts["work_order_id"])
            if not job:
                continue
            status = move.posting.state if hasattr(move, "posting") else "pending"
            yield dict(reference=f"WO-{job.order_id}", description=job.asset.tag + " · " + move.item.name,
                       site_id=job.order.site_id, date=move.posting.entry.date if metric == "maintenance-posted" else move.business_date,
                       status=status, amount=-move.value, issued=-move.value if move.kind == "maint_issue" else ZERO,
                       returned=move.value if move.kind == "maint_return" else ZERO, path=f"/work-orders/{job.order_id}")
        return
    if metric == "maintenance-open":
        for job in jobs.exclude(stage__in=("closed", "cancelled")).order_by("pk").iterator():
            yield job_row(job)
        return
    plans = MaintenancePlan.objects.filter(asset__in=assets).select_related("asset")
    now = timezone.now()
    settings = {row.site_id: row for row in SiteSettings.objects.all()}
    if metric == "pm-due":
        retired = AssetProfile.objects.filter(state="retired").values("asset_id")
        for plan in plans.filter(active=True).exclude(asset_id__in=retired).order_by("pk").iterator():
            day = next_due(plan)
            local = business_date(settings.get(plan.asset.site_id), now)
            if day and day <= local:
                yield plan_row(plan, day, "overdue" if day < local else "due")
        return
    start, end = date.fromisoformat(filters["from"]), date.fromisoformat(filters["to"])
    actual = defaultdict(dict)
    for job in span(jobs.filter(plan__isnull=False), {"from": start, "to": end}, "due_date").iterator():
        actual[job.plan_id][job.due_date] = job
    retired = set(AssetProfile.objects.filter(state="retired").values_list("asset_id", flat=True))
    current_plan_ids = set(plans.values_list("pk", flat=True))
    for job_entries in actual.values():
        for day, job in job_entries.items():
            if job.plan_id not in current_plan_ids:
                completed = business_date(settings.get(job.order.site_id), job.completed_at) if job.completed_at else None
                yield {**job_row(job), "on_time": int(bool(completed and completed <= day)), "date": completed}
    for plan in plans.order_by("pk").iterator():
        site_calendar = settings.get(plan.asset.site_id)
        through = min(end, business_date(site_calendar, now))
        entries = actual[plan.pk]
        days = {day for day in entries if day <= through}
        if plan.active and plan.asset_id not in retired:
            if plan.frequency == "months":
                offset = (start.year - plan.first_due.year) * 12 + start.month - plan.first_due.month
            else:
                offset = (start - plan.first_due).days // (7 if plan.frequency == "weeks" else 1)
            index = max(0, offset // plan.interval)
            while True:
                try:
                    day = due_on(plan, index)
                except OverflowError:
                    break
                if not day or day > through:
                    break
                if day >= start:
                    days.add(day)
                index += 1
        for day in sorted(days):
            job = entries.get(day)
            completed = business_date(settings.get(job.order.site_id), job.completed_at) if job and job.completed_at else None
            on_time = bool(completed and completed <= day)
            row = job_row(job) if job else plan_row(plan, day, "not_generated")
            yield {**row, "on_time": int(on_time), "date": completed}


def plan_row(plan, day, status):
    return dict(reference=f"PM-{plan.pk}", description=plan.title, site_id=plan.asset.site_id,
                due_date=day, status=status, path=f"/maintenance/assets/{plan.asset_id}")


def job_row(job):
    return dict(reference=f"WO-{job.order_id}", description=job.order.title, site_id=job.order.site_id,
                due_date=job.due_date, status=job.stage, path=f"/work-orders/{job.order_id}")


def data(user, metric, filters, extra=()):
    if metric == "ap-aging":
        return ap_rows(user, filters, extra)
    if metric == "ar-aging":
        return ar_rows(user, filters, extra)
    if metric == "grni":
        return grni_rows(user, filters, extra)
    if metric == "pending-postings":
        return pending_rows(user, filters, extra)
    if metric == "stock-value":
        return stock_rows(user, filters, extra)
    if METRICS[metric] == "stock":
        return purchase_rows(user, filters, extra, metric)
    if metric == "net-posted-sales":
        return net_posted_sales(user, filters, extra)
    if METRICS[metric] == "sales":
        return sales_rows(user, filters, extra, metric)
    return maintenance_rows(user, filters, extra, metric)


def effective(metric, filters):
    keys = {"site", *RELEVANT[metric]}
    if metric in AS_OF:
        keys.add("as_of")
    if metric in AGING:
        keys.add("bucket")
    if metric in (*AGING, "net-posted-sales", "collections"):
        keys.add("currency")
    if metric in RANGE:
        keys.update(("from", "to"))
    return {key: value for key, value in filters.items() if key in keys}


def basis(metric):
    return "as_of" if metric in AS_OF else "period" if metric in RANGE else "current"


def summarize(user, metric, filters, *, page=None, size=100, currency_groups=False):
    count, on_time = 0, 0
    amount = issued = returned = ZERO
    groups = defaultdict(lambda: {"count": 0, "amount": ZERO})
    items = []
    currencies = defaultdict(lambda: ZERO)
    by_currency = defaultdict(lambda: defaultdict(lambda: ZERO))
    for row in data(user, metric, filters):
        if "bucket" in filters and row.get("status") != filters["bucket"]:
            continue
        count += 1
        on_time += row.get("on_time", 0)
        amount += row.get("amount", ZERO)
        issued += row.get("issued", ZERO)
        returned += row.get("returned", ZERO)
        if "currency" in row:
            currencies[row["currency"]] += row["amount"]
            if currency_groups:
                by_currency[row["currency"]][row["status"]] += row["amount"]
        if row.get("status"):
            group = groups[row["status"]]
            group["count"] += 1
            group["amount"] += row.get("amount", ZERO)
        if page is not None and (page - 1) * size < count <= page * size:
            items.append(row)
    result = {"metric": metric, "filters": filters, "basis": basis(metric), "count": count, "groups": groups,
              "amount": format(amount, ".2f") if metric in MONEY else None}
    for group in groups.values():
        group["amount"] = format(group["amount"], ".2f")
    if metric == "net-posted-sales":
        result["amount"] = None
    if metric in (*AGING, "net-posted-sales", "collections", "grni"):
        result["currencies"] = {key: format(value, ".2f") for key, value in sorted(currencies.items())}
    if currency_groups:
        result["currency_groups"] = {key: {group: format(value, ".2f") for group, value in values.items()}
                                     for key, values in sorted(by_currency.items())}
    if metric == "pm-on-time":
        result["on_time"] = on_time
    if metric in ("maintenance-parts", "maintenance-posted"):
        result.update(issued=format(issued, ".2f"), returned=format(returned, ".2f"))
    if page is not None:
        result.update(items=items, page=page, page_size=size)
    return result


def filter_options(user, metrics):
    groups = {}
    for key, model, metric, source, id_field in (
        ("supplier", Supplier, "ap-aging", SupplierInvoice.objects.all(), "supplier_id"),
        ("customer", Customer, "sales-invoices", Document.objects.all(), "customer_id"),
        ("asset", Asset, "maintenance-open", Asset.objects.all(), "pk"),
        ("item", Item, "stock-value", Valuation.objects.all(), "item_id"),
    ):
        sources = [(metric, source, id_field)]
        if key == "supplier":
            sources.append(("open-purchases", PurchaseOrder.objects.filter(status="issued"), "supplier_id"))
            sources.append(("grni", PurchaseOrder.objects.filter(status="issued"), "supplier_id"))
        if key == "customer":
            sources.append(("ar-aging", source, id_field))
        ids = set()
        for matched, rows, field in sources:
            if matched in metrics:
                additional = ("workorders.view",) if key == "asset" else ()
                ids.update(scoped(rows, user, METRIC_ACCESS[matched][1], {}, extra=additional)
                           .order_by().values_list(field, flat=True).distinct())
        if any(matched in metrics for matched, _, _ in sources):
            groups[key] = list(model.objects.filter(pk__in=ids).order_by("name", "pk").values("id", "name"))
    # GL readers can select any detail account. AP and sales readers use the accounts
    # exposed by their subledger rows; a numeric filter remains supported there.
    if {"ap-aging", "ar-aging", "grni"}.intersection(metrics) and has_permission(user, "accounting.view", None):
        groups["account"] = [{"id": key, "name": f"{code} · {name}"} for key, code, name
                             in Account.objects.filter(detail=True).order_by("code").values_list("pk", "code", "name")]
    return groups


@never_cache
@endpoint("GET")
def dashboards(request, board):
    filters = valid_filters(board, request.GET.dict(), defaults=True)
    enabled = enabled_modules()
    metrics = [metric for metric in BOARDS[board] if available(request.user, metric, enabled)]
    if not metrics:
        raise Problem("forbidden", 403)
    return {"board": board, "filters": filters, "options": filter_options(request.user, metrics),
            "cards": [summarize(request.user, metric, effective(metric, filters)) for metric in metrics]}


@never_cache
@endpoint("GET")
def detail(request, metric):
    board = METRICS.get(metric)
    query = request.GET.dict()
    size=query.pop("page_size","100")
    if size not in ("10","25","50","100"):raise Problem("report_invalid_filters")
    page = query.pop("page", "1")
    if not page.isascii() or not page.isdecimal() or len(page) > 6 or not int(page):
        raise Problem("report_invalid_filters")
    filters = effective(metric, valid_filters(board, query, defaults=True, metric=metric))
    if not available(request.user, metric, enabled_modules()):
        raise Problem("forbidden", 403)
    return summarize(request.user, metric, filters, page=int(page),size=int(size))


@never_cache
@endpoint("GET")
def export(request, metric):
    filters = effective(metric, valid_filters(METRICS.get(metric), request.GET.dict(), defaults=True, metric=metric))
    require(request.user, "platform.export")
    if not available(request.user, metric, enabled_modules()):
        raise Problem("forbidden", 403)
    rows = data(request.user, metric, filters, ("platform.export",))
    if "bucket" in filters:
        rows = (row for row in rows if row.get("status") == filters["bucket"])
    return csv_response(metric, COLUMNS, ([row.get(key) for key in COLUMNS] for row in rows))
