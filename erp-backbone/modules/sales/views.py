import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from accounting.models import Posting
from accounting.centers import center_choices, center_for_site
from accounting.services import ledger_lock, process
from accounting.views import day_field
from backbone.validation import field_path
from backbone.access import audit, visible
from backbone.http import Problem, body, endpoint, identifier, once, require, string
from backbone.services.pagination import directory_rows, paginate
from inventory.models import Item, Location, Movement, SiteSettings
from inventory.stock import business_date, locked_site, prorate, record, rounded
from purchasing.money import decimal_value
from .models import Customer, Document, PostingEvent

MODULE = "sales"
KIND_PERMISSION = {"quote": "quote", "order": "order", "delivery": "deliver", "invoice": "invoice",
                   "receipt": "collect", "return": "return", "credit": "credit"}


def customer_data(row):
    return {key: getattr(row, key) for key in ("id", "code", "name", "legal_name", "contact",
            "billing", "shipping", "tax_id", "terms", "active", "revision")}


def customer_values(data):
    terms = data.get("terms", 30)
    active = data.get("active", True)
    if type(terms) is not int or not 0 <= terms <= 365 or type(active) is not bool:
        raise Problem("invalid_customer")
    values = {key: string(data, key, length, required=key == "name") for key, length in
              (("name", 120), ("legal_name", 120), ("contact", 120), ("billing", 240),
               ("shipping", 240), ("tax_id", 60))}
    return {**values, "terms": terms, "active": active}


@endpoint("GET", "POST", module="customers")
def customers(request):
    user = request.user
    require(user, "sales.view")
    if request.method == "GET":
        rows = Customer.objects.filter(is_deleted=False).order_by("code")
        if "page" in request.GET:
            return paginate(request, directory_rows(rows, request.GET), customer_data)
        return {"items": [customer_data(row) for row in rows]}
    require(user, "sales.customer", None)
    data = body(request)
    code = string(data, "code", 24).upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]*", code):
        raise Problem("invalid_code")
    values = customer_values(data)
    with transaction.atomic():
        if data.get("id") is None:
            row, created = Customer.objects.get_or_create(code=code, defaults=values)
            if not created:
                raise Problem("sales_customer_code_exists", 409)
        else:
            row = Customer.objects.select_for_update().filter(pk=identifier(data["id"], "customer"), is_deleted=False).first()
            if row is None:
                raise Problem("not_found", 404)
            if row.code != code or row.revision != data.get("revision"):
                raise Problem("revision_conflict", 409)
            for key, value in values.items():
                setattr(row, key, value)
            row.revision += 1
            row.save()
            created = False
        audit(user, "sales.customer", row.code)
        return customer_data(row), 201 if created else 200


def scoped(user, pk, kind=None, lock=False):
    rows = visible(Document.objects.all(), user, "sales.view")
    if kind:
        rows = rows.filter(kind=kind)
    if lock:
        rows = rows.select_for_update()
    row = rows.filter(pk=identifier(pk, "source")).first()
    if row is None:
        raise Problem("not_found", 404)
    return row


def document_status(row):
    if row.kind == "quote" and row.status in ("draft", "sent", "accepted") and row.expires < timezone.localdate():
        return "expired"
    return row.status


def summary(row, progress=False):
    posting = Posting.objects.filter(sale__document=row).values("entry_id", "state", "error").first()
    result = {"id": row.pk, "kind": row.kind, "number": row.number, "site_id": row.site_id,
              "customer_id": row.customer_id, "customer": row.snapshot.get("customer", {}).get("name", row.customer.name),
              "source_id": row.source_id, "source_number": row.source.number if row.source_id else None, "order_id": row.order_id, "date": row.date.isoformat(),
              "due": row.due.isoformat() if row.due else None, "expires": row.expires.isoformat() if row.expires else None,
              "status": document_status(row), "revision": row.revision, "amount": str(row.amount),
              "reason": row.reason, "lines": row.lines, "snapshot": row.snapshot, "posting": posting}
    if progress:
        settings = SiteSettings.objects.filter(site_id=row.site_id).first()
        result["business_date"] = business_date(settings, timezone.now()).isoformat()
        result["children"] = [{"id": item.pk, "number": item.number, "kind": item.kind}
                              for item in row.children.order_by("pk")]
        if row.kind == "order":
            result["progress"] = order_progress(row)
            result["cost_centers"] = center_choices(row.site_id)
            result["cost_center_editable"] = row.status in ("draft", "confirmed") and not row.documents.filter(kind="delivery").exists()
        elif row.kind == "invoice":
            result["outstanding"] = str(invoice_balance(row))
    return result


def entries(data):
    lines = data.get("lines")
    if not isinstance(lines, list) or not 1 <= len(lines) <= 100 or not all(isinstance(line, dict) for line in lines):
        raise Problem("invalid_lines")
    return lines


def quantity(line):
    value = decimal_value(line.get("quantity"), 7, 3, "quantity")
    if value <= 0:
        raise Problem("invalid_quantity", field="quantity", reason="min_value")
    return value


def by_line(doc):
    return {line["id"]: line for line in doc.lines}


def picked(doc, data):
    known, used = by_line(doc), set()
    for index, line in enumerate(entries(data)):
        with field_path(f"lines.{index}"):
            key = identifier(line.get("line_id"), "line")
            if key not in known or key in used:
                raise Problem("invalid_lines")
            used.add(key)
            yield known[key], line, quantity(line)


def quote_lines(data):
    result = []
    for index, line in enumerate(entries(data), 1):
        with field_path(f"lines.{index - 1}"):
            item = Item.objects.filter(pk=identifier(line.get("item_id"), "item"), active=True, is_deleted=False).select_related("base_unit").first()
            if item is None:
                raise Problem("invalid_item")
            qty = quantity(line)
            price = decimal_value(line.get("price"), 9, 4, "price")
            if price <= 0 or rounded(qty * price) <= 0:
                raise Problem("invalid_price", field="price", reason="min_value")
            result.append({"id": index, "item_id": item.pk, "name": item.name, "unit": item.base_unit.code,
                           "quantity": str(qty), "price": str(price), "amount": str(rounded(qty * price))})
    return result


def invoice_balance(invoice):
    remaining = invoice.amount
    for row in Document.objects.filter(customer_id=invoice.customer_id, site_id=invoice.site_id,
                                       kind__in=("credit", "receipt")):
        if row.kind == "credit" and row.source_id == invoice.pk:
            remaining -= row.amount
        elif row.kind == "receipt":
            remaining -= sum((Decimal(line["amount"]) for line in row.lines if line["invoice_id"] == invoice.pk), Decimal(0))
    return remaining


def order_progress(order):
    delivered, invoiced = defaultdict(Decimal), defaultdict(Decimal)
    for row in order.documents.filter(kind__in=("delivery", "return", "invoice")):
        for line in row.lines:
            key = line["order_line_id"]
            qty = Decimal(line["quantity"])
            if row.kind == "invoice":
                invoiced[key] += qty
            else:
                delivered[key] += qty if row.kind == "delivery" else -qty
    return [{"line_id": line["id"], "delivered": str(delivered[line["id"]]), "invoiced": str(invoiced[line["id"]]),
             "remaining": str(Decimal(line["quantity"]) - delivered[line["id"]]) if order.status != "cancelled" else "0"}
            for line in order.lines]


def consumed(rows, key, line_id):
    return sum((Decimal(line["quantity"]) for row in rows for line in row.lines if line[key] == line_id), Decimal(0))


def amount_slice(value, total, prior, qty):
    return prorate(value, prior + qty, total) - prorate(value, prior, total)


def dated(data, source=None):
    day = day_field(data, "date")
    if day > timezone.localdate() + timedelta(days=1):
        raise Problem("invalid_date")
    if source and day < source.date:
        raise Problem("finance_before_original", 409)
    return day


def create(user, kind, site_id, customer, day, lines, **extra):
    row = Document.objects.create(kind=kind, site_id=site_id, customer=customer, date=day,
        lines=lines, amount=sum((Decimal(line["amount"]) for line in lines), Decimal(0)),
        actor=user.get_username(), **extra)
    audit(user, "sales." + kind, f"{row.number} · {row.reason}")
    return row


def post_document(row, ledger, dependencies=()):
    event = PostingEvent.objects.create(document=row)
    event.prerequisites.add(*[source.postingevent for source in dependencies])
    return process(Posting.objects.create(ledger=ledger, sale=event), "system")


def active_ledger(day):
    ledger = ledger_lock()
    if not ledger.activated_at or day < ledger.cutoff:
        raise Problem("sales_cutover_required", 409)
    return ledger


def quote(user, data):
    site_id = identifier(data.get("site_id"), "site")
    require(user, "sales.view", site_id)
    require(user, "sales.quote", site_id)
    from backbone.site_admin import require_active_site

    require_active_site(site_id, lock=True)
    customer = Customer.objects.filter(pk=identifier(data.get("customer_id"), "customer"), active=True, is_deleted=False).first()
    if not customer:
        raise Problem("invalid_customer")
    day, expires = dated(data), day_field(data, "expires")
    if expires < day:
        raise Problem("invalid_expires")
    return create(user, "quote", site_id, customer, day, quote_lines(data), status="draft", expires=expires)


def delivery(user, order, data):
    if order.status != "confirmed":
        raise Problem("sales_order_not_confirmed", 409)
    settings = locked_site(order.site_id)
    day = dated(data, order)
    ledger = active_ledger(day)
    center = center_for_site(order.snapshot.get("cost_center_id"), order.site_id)
    progress = {row["line_id"]: row for row in order_progress(order)}
    net_sales = defaultdict(Decimal)
    for doc in order.documents.filter(kind__in=("delivery", "return")):
        for line in doc.lines:
            net_sales[line["order_line_id"]] += (Decimal(line["amount"]) if doc.kind == "delivery"
                                                 else -Decimal(line["original_sales_value"]))
    lines = []
    # The order lock serializes fulfilment and the site lock serializes withdrawals across orders.
    for source, line, qty in picked(order, data):
        row = progress[source["id"]]
        if qty > Decimal(row["remaining"]):
            raise Problem("sales_delivery_excess", 409)
        location = Location.objects.filter(pk=identifier(line.get("location_id"), "location"),
                                           active=True, is_deleted=False,
                                           warehouse__site_id=order.site_id, warehouse__active=True,
                                           warehouse__is_deleted=False).first()
        if location is None:
            raise Problem("invalid_location")
        prior = Decimal(row["delivered"])
        total = Decimal(source["quantity"])
        sales_value = prorate(Decimal(source["amount"]), prior + qty, total) - net_sales[source["id"]]
        if sales_value <= 0:
            raise Problem("sales_amount_too_small", 409)
        move = record(user, settings, Item.objects.get(pk=source["item_id"]), location, "sale", -qty, Decimal(0),
                      {"sales_order_id": order.pk, "order_line_id": source["id"]})
        from supply.reservations import release_excess
        release_excess(order, source["id"], total - prior - qty)
        # The shipment date must be the stock business date, including the site's day start.
        if day != move.business_date:
            raise Problem("sales_delivery_business_date", 409)
        lines.append({**source, "order_line_id": source["id"], "quantity": str(qty), "amount": str(sales_value),
                      "cost": str(-move.value), "location_id": location.pk, "movement_id": move.pk})
    row = create(user, "delivery", order.site_id, order.customer, day, lines, source=order, order=order, status="confirmed",
                 snapshot={"cost_center_id": center.pk if center else None})
    post_document(row, ledger)
    return row


def invoice(user, shipped, data):
    day = dated(data, shipped)
    ledger = active_ledger(day)
    previous = list(shipped.children.filter(kind="invoice"))
    unbilled_returns = list(shipped.children.filter(kind="return", snapshot__credited=False))
    lines = []
    for source, _, qty in picked(shipped, data):
        prior = consumed(previous, "delivery_line_id", source["id"])
        returned = consumed(unbilled_returns, "delivery_line_id", source["id"])
        total = Decimal(source["quantity"])
        if qty + prior + returned > total:
            raise Problem("sales_invoice_excess", 409)
        amount = amount_slice(Decimal(source["amount"]), total, prior + returned, qty)
        if amount <= 0:
            raise Problem("sales_amount_too_small", 409)
        lines.append({**source, "delivery_line_id": source["id"], "quantity": str(qty), "amount": str(amount)})
    customer = shipped.customer
    terms = shipped.order.snapshot["customer"]["terms"]
    if day > date.max - timedelta(days=terms):
        raise Problem("invalid_date")
    row = create(user, "invoice", shipped.site_id, customer, day, lines, source=shipped, order=shipped.order,
                 due=day + timedelta(days=terms), snapshot={"customer": customer_data(customer)}, status="issued")
    post_document(row, ledger, [shipped])
    return row


def receipt(user, first, data):
    day = dated(data, first)
    ledger = active_ledger(day)
    raw_lines = entries(data)
    requested_ids = []
    for index, line in enumerate(raw_lines):
        with field_path(f"lines.{index}"):
            inv_id = identifier(line.get("invoice_id"), "invoice")
            if inv_id in requested_ids:
                raise Problem("sales_invalid_allocation", 409)
            requested_ids.append(inv_id)
    locked_invoices = {
        inv.pk: inv
        for inv in visible(Document.objects.filter(kind="invoice", pk__in=requested_ids).order_by("pk").select_for_update(), user, "sales.view")
    }
    lines, seen, sources = [], set(), []
    for index, line in enumerate(raw_lines):
        with field_path(f"lines.{index}"):
            inv_id = requested_ids[index]
            row = locked_invoices.get(inv_id)
            if row is None:
                raise Problem("not_found", 404)
            value = decimal_value(line.get("amount"), 15, 2, "amount")
            if (row.pk in seen or row.customer_id != first.customer_id or row.site_id != first.site_id or
                    row.date > day or value <= 0 or value > invoice_balance(row)):
                raise Problem("sales_invalid_allocation", 409)
            seen.add(row.pk)
            sources.append(row)
            lines.append({"invoice_id": row.pk, "amount": str(value)})
    if first.pk not in seen:
        raise Problem("sales_invalid_allocation", 409)
    row = create(user, "receipt", first.site_id, first.customer, day, lines, source=first, status="recorded",
                 reason=string(data, "reason", 1000, required=False))
    post_document(row, ledger, sources)
    return row


def credit(user, bill, data):
    day = dated(data, bill)
    ledger = active_ledger(day)
    prior_credits = list(bill.children.filter(kind="credit"))
    lines = []
    for source, _, qty in picked(bill, data):
        prior = consumed(prior_credits, "invoice_line_id", source["id"])
        total = Decimal(source["quantity"])
        if prior + qty > total:
            raise Problem("sales_credit_excess", 409)
        amount = amount_slice(Decimal(source["amount"]), total, prior, qty)
        if amount <= 0:
            raise Problem("sales_amount_too_small", 409)
        lines.append({**source, "invoice_line_id": source["id"], "quantity": str(qty), "amount": str(amount)})
    row = create(user, "credit", bill.site_id, bill.customer, day, lines, source=bill, order=bill.order,
                 snapshot=bill.snapshot, status="issued", reason=string(data, "reason", 1000))
    post_document(row, ledger, [bill])
    return row


def returned(user, source, data):
    credited = source.kind == "credit"
    shipped = source.source.source if credited else source
    settings = locked_site(shipped.site_id)
    day = dated(data, source)
    ledger = active_ledger(day)
    returns = list(shipped.order.documents.filter(kind="return", snapshot__delivery_id=shipped.pk))
    billed = list(shipped.children.filter(kind="invoice"))
    lines = []
    for selected, _, qty in picked(source, data):
        original = by_line(shipped)[selected["delivery_line_id"]] if credited else selected
        total = Decimal(original["quantity"])
        prior = consumed(returns, "delivery_line_id", original["id"])
        if credited:
            limit = Decimal(selected["quantity"]) - consumed(source.children.filter(kind="return"), "source_line_id", selected["id"])
        else:
            unbilled_returns = [row for row in returns if not row.snapshot["credited"]]
            limit = total - consumed(billed, "delivery_line_id", original["id"]) - consumed(
                unbilled_returns, "delivery_line_id", original["id"])
        if qty > limit or qty + prior > total:
            raise Problem("sales_return_excess", 409)
        move = Movement.objects.get(pk=original["movement_id"])
        cost = amount_slice(-move.value, total, prior, qty)
        inbound = record(user, settings, move.item, move.location, "sale_return", qty, cost,
            {"sales_order_id": shipped.order_id, "order_line_id": original["order_line_id"], "delivery_id": shipped.pk}, original=move)
        if day != inbound.business_date:
            raise Problem("sales_delivery_business_date", 409)
        billed_quantity = consumed(billed, "delivery_line_id", original["id"])
        unbilled_prior = consumed([row for row in returns if not row.snapshot["credited"]], "delivery_line_id", original["id"])
        if credited:
            applied = consumed(source.children.filter(kind="return"), "source_line_id", selected["id"])
            original_sales = amount_slice(Decimal(selected["amount"]), Decimal(selected["quantity"]), applied, qty)
            value = Decimal(0)
        else:
            value = amount_slice(Decimal(original["amount"]), total, billed_quantity + unbilled_prior, qty)
            original_sales = value
        lines.append({**original, "source_line_id": selected["id"], "delivery_line_id": original["id"],
                      "quantity": str(qty), "amount": str(value), "cost": str(cost),
                      "original_sales_value": str(original_sales), "movement_id": inbound.pk})
    row = create(user, "return", shipped.site_id, shipped.customer, day, lines, source=source, order=shipped.order,
        status="confirmed", reason=string(data, "reason", 1000), snapshot={"delivery_id": shipped.pk, "credited": credited,
            "cost_center_id": shipped.snapshot.get("cost_center_id")})
    post_document(row, ledger, [source])
    return row


def document_rows(user, filters):
    require(user, "sales.view")
    rows = visible(Document.objects.select_related("customer", "source").order_by("-id"), user, "sales.view")
    return rows.filter(kind=filters["kind"]) if filters.get("kind") else rows


@endpoint("GET", "POST", module=MODULE)
def documents(request):
    user = request.user
    require(user, "sales.view")
    if request.method == "GET":
        rows = document_rows(user, request.GET)
        before = request.GET.get("before")
        if before:
            if not before.isascii() or not before.isdecimal() or len(before) > 18:
                raise Problem("invalid_cursor")
            rows = rows.filter(pk__lt=int(before))
        if "page_size" in request.GET:
            from backbone.services.pagination import operational_page
            return {**operational_page(request,rows,summary,['kind', 'customer__name', 'status'])}
        result = list(rows[:101])
        return {"items": [summary(row) for row in result[:100]], "next_before": result[99].pk if len(result) > 100 else None}
    data = body(request)
    kind = data.get("kind")
    if not isinstance(kind, str) or kind not in KIND_PERMISSION:
        raise Problem("invalid_kind")
    if kind != "quote":
        source = scoped(user, data.get("source_id"))
        require(user, "sales." + KIND_PERMISSION[kind], source.site_id)
    else:
        site_id = identifier(data.get("site_id"), "site")
        require(user, "sales.view", site_id)
        require(user, "sales.quote", site_id)

    def commit():
        if kind == "quote":
            row = quote(user, data)
            return summary(row, True)
        # All financial and stock actions for an order take its row lock first.
        anchor = scoped(user, source.order_id or source.pk, lock=True)
        current = anchor if source.pk == anchor.pk else scoped(user, source.pk, lock=True)
        allowed = {"order": ("quote",), "delivery": ("order",), "invoice": ("delivery",),
                   "receipt": ("invoice",), "credit": ("invoice",), "return": ("delivery", "credit")}
        if current.kind not in allowed[kind]:
            raise Problem("invalid_source")
        if kind == "order":
            from backbone.site_admin import require_active_site

            require_active_site(current.site_id, lock=True)
            if (current.status != "accepted" or current.expires < max(timezone.localdate(), dated(data, current))
                    or current.children.filter(kind="order").exists() or not current.customer.active or current.customer.is_deleted):
                raise Problem("sales_quote_unavailable", 409)
            row = create(user, "order", current.site_id, current.customer, dated(data, current), current.lines,
                         source=current, status="draft", snapshot={"customer": customer_data(current.customer)})
        else:
            actions = {"delivery": delivery, "invoice": invoice, "receipt": receipt, "credit": credit, "return": returned}
            row = actions[kind](user, current, data)
        return summary(row, True)

    return once(request, "sales.document.create", data, commit)


@endpoint("GET", "PATCH", module=MODULE)
def document(request, pk):
    user = request.user
    if request.method == "GET":
        return summary(scoped(user, pk), True)
    data = body(request)
    with transaction.atomic():
        from backbone.site_admin import require_active_site
        row = scoped(user, pk, lock=True)
        require_active_site(row.site_id, lock=True)
        require(user, "sales." + KIND_PERMISSION[row.kind], row.site_id)
        if identifier(data.get("revision"), "revision") != row.revision:
            raise Problem("revision_conflict", 409)
        action = data.get("action")
        states = {("quote", "draft", "send"): "sent", ("quote", "sent", "accept"): "accepted",
                  ("quote", "draft", "cancel"): "cancelled", ("quote", "sent", "cancel"): "cancelled",
                  ("quote", "accepted", "cancel"): "cancelled",
                  ("order", "draft", "confirm"): "confirmed", ("order", "draft", "cancel"): "cancelled",
                  ("order", "confirmed", "cancel"): "cancelled"}
        if not isinstance(action, str):
            raise Problem("invalid_action")
        if action == "cost_center" and row.kind == "order" and row.status in ("draft", "confirmed") and not row.documents.filter(kind="delivery").exists():
            center = center_for_site(data.get("cost_center_id"), row.site_id)
            row.snapshot = {**row.snapshot, "cost_center_id": center.pk if center else None}
        elif action == "edit" and row.kind == "quote" and row.status == "draft":
            row.lines = quote_lines(data)
            row.amount = sum(Decimal(line["amount"]) for line in row.lines)
        else:
            state = states.get((row.kind, row.status, action))
            if not state or row.kind == "quote" and row.children.exists():
                raise Problem("sales_invalid_state", 409)
            if row.kind == "quote" and action in ("send", "accept") and row.expires < timezone.localdate():
                raise Problem("sales_quote_expired", 409)
            if action == "cancel":
                row.reason = string(data, "reason", 1000)
                if row.kind == "order":
                    from supply.models import Reservation

                    live = Reservation.objects.filter(order=row, quantity__gt=0)
                    if live.exists():
                        locked_site(row.site_id)
                        live.update(quantity=0)
            if row.kind == "quote" and action == "send":
                row.snapshot = {"customer": customer_data(row.customer)}
            if row.kind == "order" and action == "confirm":
                if not row.customer.active or row.customer.is_deleted:
                    raise Problem("invalid_customer")
                row.snapshot = {**row.snapshot, "customer": customer_data(row.customer)}
            row.status = state
        row.revision += 1
        row.save()
        audit(user, "sales." + action, f"{row.number} · {row.reason}")
        return summary(row, True)


@endpoint("GET", module=MODULE)
def aging(request):
    user = request.user
    require(user, "sales.view")
    today = timezone.localdate()
    result = []
    rows = visible(Document.objects.filter(kind="invoice").select_related("customer"), user, "sales.view")
    for row in rows.order_by("due", "pk"):
        value = invoice_balance(row)
        if value:
            days = (today - row.due).days
            result.append({**summary(row), "outstanding": str(value),
                "bucket": "credit" if value < 0 else "current" if days <= 0 else "1–30" if days <= 30 else
                "31–60" if days <= 60 else "61–90" if days <= 90 else "91+"})
    if "page_size" in request.GET:
        from backbone.services.pagination import paginate_items
        return {"as_of":today.isoformat(),**paginate_items(request,result,("number","customer","bucket"))}
    return {"as_of": today.isoformat(), "items": result}


@endpoint("GET", module=MODULE)
def print_document(request, pk):
    from django.shortcuts import render

    row = scoped(request.user, pk)
    if row.kind not in ("quote", "invoice", "credit"):
        raise Problem("not_found", 404)
    from backbone.company_profile import branding
    from backbone.print_i18n import normalize_print_language, print_direction, print_status, print_strings, print_text
    language = normalize_print_language(request.GET.get("language") or request.GET.get("lang"), "en")
    title_key = {"quote": "quotation", "invoice": "sales_invoice", "credit": "credit_note"}[row.kind]
    data = row.snapshot.get("customer") or customer_data(row.customer)
    response = render(request, "sales/print.html", {
        "row": row,
        "customer": data,
        "company": branding(),
        "language": language,
        "dir": print_direction(language),
        "ui": print_strings(language),
        "status_label": print_status(language, row.status),
        "title": print_text(language, title_key),
    })
    response["Cache-Control"] = "private, no-store"
    return response


@endpoint("GET", module=MODULE)
def catalog(request):
    require(request.user, "sales.view")
    from inventory.models import Unit, Warehouse

    warehouses = visible(Warehouse.objects.filter(active=True, is_deleted=False).order_by("code"), request.user, "sales.view")
    return {"items": list(Item.objects.filter(active=True, is_deleted=False).order_by("code").values()),
            "units": list(Unit.objects.order_by("code").values("id", "code")),
            "warehouses": list(warehouses.values("id", "site_id", "name")),
            "locations": list(Location.objects.filter(warehouse__in=warehouses, active=True, is_deleted=False).order_by("code").values())}


@endpoint("GET", "DELETE", module="customers")
def customer(request, pk):
    require(request.user, "sales.view")
    row = Customer.objects.filter(pk=pk, is_deleted=False).first()
    if row is None:
        raise Problem("not_found", 404)
    if request.method == "DELETE":
        from backbone.deletion import perform_record_deletion
        require(request.user, "sales.customer", None)
        return perform_record_deletion(
            row,
            request.user,
            "sales.customer.delete",
            has_dependencies_fn=lambda c: Document.objects.filter(customer=c).exists(),
        )
    return customer_data(row)
