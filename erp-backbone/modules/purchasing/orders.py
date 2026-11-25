from backbone.extensions import invoke_extension
from datetime import date
import re
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from backbone.validation import field_path
from backbone.access import audit, visible
from backbone.http import Problem, body, endpoint, identifier, once, require, string
from backbone.services.documents import archive_rows, reject_archived, scoped_document
from backbone.services.numbering import allocate_number
from backbone.services.pagination import directory_rows, paginate, positive_filter, search_rows

from .models import PurchaseOrder, PurchaseReceiptLink, Requisition, Supplier
from .money import check_total, decimal_value, line_amount
from .views import CURRENCIES, check_revision, find_row


def supplier_values(data):
    code = string(data, "code", 32).upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{0,31}", code):
        raise Problem("invalid_supplier_code")
    currency = data.get("currency_default")
    if currency not in CURRENCIES or type(data.get("active")) is not bool:
        raise Problem("invalid_supplier_fields")
    return {
        "code": code, "name": string(data, "name", 120),
        "legal_name": string(data, "legal_name", 160, required=False),
        "currency_default": currency, "contact": string(data, "contact", 240, required=False),
        "tax_id": string(data, "tax_id", 60, required=False), "active": data["active"],
    }


def supplier_record(row):
    return {field: getattr(row, field) for field in (
        "id", "code", "name", "legal_name", "currency_default", "contact", "tax_id", "active", "revision",
    )}


@endpoint("GET", "POST", module="suppliers")
def suppliers(request):
    user = request.user
    if request.method == "GET":
        directory = request.GET.get("directory") == "1"
        if directory:
            from backbone.access import has_permission
            if not (has_permission(user, "suppliers.view", None) or has_permission(user, "suppliers.manage", None)):
                raise Problem("forbidden", 403)
        else:
            require(user, "purchasing.view")
        rows = Supplier.objects.filter(is_deleted=False).order_by("code")
        if directory and "page" in request.GET:
            return {**paginate(request, directory_rows(rows, request.GET), supplier_record), "currencies": CURRENCIES}
        if not directory and not user.is_superuser:
            rows = rows.filter(active=True)
            return {"items": list(rows.values("id", "code", "name")), "currencies": CURRENCIES}
        return {"items": [supplier_record(row) for row in rows], "currencies": CURRENCIES}
    require(user, "suppliers.manage", None)
    data = supplier_values(body(request))
    with transaction.atomic():
        row, created = Supplier.objects.get_or_create(code=data["code"], defaults=data)
        if not created:
            raise Problem("duplicate_supplier_code", 409)
        audit(user, "supplier.create", row.code)
        return supplier_record(row), 201


@endpoint("GET", "PATCH", "DELETE", module="suppliers")
def supplier(request, pk):
    if request.method == "DELETE":
        from backbone.deletion import perform_record_deletion
        require(request.user, "suppliers.manage", None)
        row = Supplier.objects.filter(pk=pk, is_deleted=False).first()
        if row is None:
            raise Problem("not_found", 404)
        return perform_record_deletion(
            row,
            request.user,
            "purchasing.supplier.delete",
            has_dependencies_fn=lambda s: PurchaseOrder.objects.filter(supplier=s).exists(),
        )
    if request.method == "GET":
        from backbone.access import has_permission
        if not any(has_permission(request.user, key, None) for key in ("suppliers.view", "suppliers.manage")):
            raise Problem("forbidden", 403)
        row = Supplier.objects.filter(pk=pk).first()
        if row is None:
            raise Problem("not_found", 404)
        return {"record": supplier_record(row), "currencies": CURRENCIES}
    require(request.user, "suppliers.manage", None)
    data = body(request)
    values = supplier_values(data)
    with transaction.atomic():
        row = Supplier.objects.select_for_update().filter(pk=pk).first()
        if row is None:
            raise Problem("not_found", 404)
        check_revision(data, row)
        if values["code"] != row.code:
            raise Problem("supplier_code_locked", 409)
        action = ("reactivate" if values["active"] else "archive") if row.active != values["active"] else "update"
        for key, value in values.items():
            setattr(row, key, value)
        row.revision += 1
        row.save()
        audit(request.user, "supplier." + action, row.code)
        return supplier_record(row)


def active_supplier(pk):
    supplier_id = identifier(pk, "supplier")
    row = Supplier.objects.select_for_update().filter(pk=supplier_id, active=True).first()
    if row is None:
        raise Problem("supplier_unavailable", 409)
    return row


def summary(row):
    return {
        "id": row.pk, "number": row.number, "title": row.title, "site_id": row.site_id,
        "source_pr_id": row.source_pr_id, "source_pr_number": row.source_pr.document_number,
        "supplier_id": row.supplier_id,
        "supplier_name": row.snapshot["supplier"]["name"] if row.snapshot else row.supplier.name,
        "currency": row.currency, "total": str(row.total), "status": row.status, "revision": row.revision,
    }


def detail(row):
    return {**summary(row), "lines": row.lines, "price_changes": row.price_changes,
            "snapshot": row.snapshot, "issued_at": row.issued_at.isoformat() if row.issued_at else None,
            "issued_by": row.snapshot["issued_by"] if row.snapshot else None,
            "void_reason": row.void_reason, "voided_at": row.voided_at.isoformat() if row.voided_at else None,
            "voided_by": row.voided_by.get_username() if row.voided_by_id else None,
            "archived": archive_rows("po").filter(object_id=row.pk).exists()}


def audit_order(user, action, row):
    audit(user, f"purchase_order.{action}", f"{row.number} / {row.source_pr.document_number} / rev {row.revision}")


def order_rows(user, filters):
    require(user, "purchasing.view")
    if filters.get("ready") == "1":
        from backbone.services.inbox import issuable_orders

        selected_site = filters.get("site", "")
        if selected_site and (not selected_site.isascii() or not selected_site.isdecimal() or len(selected_site) > 18):
            raise Problem("invalid_site")
        return issuable_orders(user, int(selected_site) if selected_site else None).select_related("source_pr", "supplier").order_by("-id")
    rows = visible(PurchaseOrder.objects.select_related("source_pr", "supplier").order_by("-id"),
                   user, "purchasing.view")
    markers = archive_rows("po").values("object_id")
    rows = rows.filter(pk__in=markers) if filters.get("archived") == "1" else rows.exclude(pk__in=markers)
    rows = search_rows(rows, filters, ("number", "title", "supplier__name", "supplier__code"))
    if site_id := positive_filter(filters, "site"):
        rows = rows.filter(site_id=site_id)
    if status := filters.get("status"):
        rows = rows.filter(status=status)
    return rows


@endpoint("GET", module="purchase_orders")
def orders(request):
    rows = order_rows(request.user, request.GET)
    archived = request.GET.get("archived") == "1" and request.GET.get("ready") != "1"
    if "page" in request.GET:
        return paginate(request, rows, lambda row: {**summary(row), "archived": archived})
    return {"items": [{**summary(row), "archived": archived} for row in rows]}


@endpoint("POST", module="purchase_orders")
def convert(request, pk):
    user = request.user
    data = body(request)
    source = find_row(user, pk)
    require(user, "purchasing.po.create", source.site_id)
    supplier_id = identifier(data.get("supplier_id"), "supplier")

    def create():
        from backbone.site_admin import require_active_site

        source = find_row(user, pk, lock=True)
        require_active_site(source.site_id, lock=True)
        require(user, "purchasing.po.create", source.site_id)
        if source.status != Requisition.Status.APPROVED:
            raise Problem("purchase_request_not_approved", 409)
        reject_archived("pr", source)
        if PurchaseOrder.objects.filter(source_pr=source).exists():
            raise Problem("purchase_request_already_converted", 409)
        selected = active_supplier(supplier_id)
        number = allocate_number("po", source.site_id)
        lines = [{**line, "id": idx, "source_pr_line": idx, "estimated_unit_price": line["unit_price"]}
                 for idx, line in enumerate(source.lines, start=1)]
        row = PurchaseOrder.objects.create(number=number, source_pr=source, site_id=source.site_id,
                                           supplier=selected, title=source.title, currency=source.currency,
                                           lines=lines, total=source.total)
        audit_order(user, "create", row)
        return detail(row)

    return once(request, "purchase_order.create", {"source_pr_id": pk, "supplier_id": supplier_id}, create)


def locked_order(user, pk, permission, data, status):
    _, row = scoped_document(user, "po", pk, lock=True)
    require(user, permission, row.site_id)
    reject_archived("po", row)
    check_revision(data, row)
    if row.status != status:
        code = "purchase_order_not_draft" if status == PurchaseOrder.Status.DRAFT else "purchase_order_not_issued"
        raise Problem(code, 409)
    return row


def changed_prices(row, entries):
    if not isinstance(entries, list) or len(entries) != len(row.lines):
        raise Problem("invalid_prices")
    prices, deliveries = {}, {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise Problem("invalid_prices")
        pk = identifier(entry.get("id"), "line")
        if pk in prices:
            raise Problem("invalid_prices")
        with field_path(f"prices.{index}"):
            prices[pk] = decimal_value(entry.get("unit_price"), 9, 4, "unit_price")
        deliveries[pk] = (index, entry.get("delivery_due"))
    if prices.keys() != {line["id"] for line in row.lines}:
        raise Problem("invalid_prices")
    updated = []
    changes = []
    total = Decimal("0.00")
    for line in row.lines:
        price = prices[line["id"]]
        amount = line_amount(Decimal(line["quantity"]), price)
        total += amount
        after = format(price, ".4f")
        before = line["unit_price"]
        if price != Decimal(before):
            changes.append({"line_id": line["id"], "from": before, "to": after,
                            "delta": format(price - Decimal(before), ".4f"),
                            "estimated_unit_price": line["estimated_unit_price"]})
        updated.append({**line, "unit_price": after, "amount": str(amount)})
    check_total(total)
    for line in updated:
        index, delivery = deliveries[line["id"]]
        if delivery is None:
            continue
        if delivery == "":
            line.pop("delivery_due", None)
            continue
        try:
            if date.fromisoformat(delivery).isoformat() != delivery:
                raise ValueError
        except (ValueError, TypeError):
            raise Problem("invalid_delivery_due", field=f"prices.{index}.delivery_due", reason="invalid_date") from None
        line["delivery_due"] = delivery
    return updated, changes, total


@endpoint("GET", "PATCH", module="purchase_orders")
def order(request, pk):
    user = request.user
    if request.method == "GET":
        _, row = scoped_document(user, "po", pk)
        return detail(row)
    data = body(request)
    with transaction.atomic():
        row = locked_order(user, pk, "purchasing.po.create", data, PurchaseOrder.Status.DRAFT)
        lines, changes, total = changed_prices(row, data.get("prices"))
        supplier_id = identifier(data.get("supplier_id"), "supplier")
        reason = string(data, "price_change_reason", 1000, required=bool(changes))
        if row.supplier_id != supplier_id:
            row.supplier = active_supplier(supplier_id)
        date_changes = [str(after["id"]) for before, after in zip(row.lines, lines, strict=True)
                        if before.get("delivery_due") != after.get("delivery_due")]
        row.lines, row.total = lines, total
        row.revision += 1
        if changes:
            row.price_changes.append({"revision": row.revision, "actor": user.get_username(),
                                      "at": timezone.now().isoformat(), "reason": reason, "changes": changes})
        row.save(update_fields=["supplier", "lines", "total", "revision", "price_changes"])
        audit_order(user, "price_change" if changes else "edit", row)
        if date_changes:
            audit(user, "purchase_order.delivery_date", f"{row.number} / rev {row.revision} / {', '.join(date_changes)}")
        return detail(row)


@endpoint("POST", module="purchase_orders")
def issue(request, pk):
    user = request.user
    data = body(request)
    with transaction.atomic():
        row = locked_order(user, pk, "purchasing.po.issue", data, PurchaseOrder.Status.DRAFT)
        invoke_extension("project_purchase_new",row.pk)
        supplier = active_supplier(row.supplier_id)
        if row.total <= 0:
            raise Problem("invalid_total")
        row.issued_by = user
        row.issued_at = timezone.now()
        row.status = PurchaseOrder.Status.ISSUED
        row.revision += 1
        row.snapshot = {
            "snapshot_version": 1, "id": row.pk, "number": row.number, "title": row.title,
            "source_pr_id": row.source_pr_id, "source_pr_number": row.source_pr.document_number,
            "site_id": row.site_id, "site_name": row.site.name, "currency": row.currency,
            "supplier": supplier_record(supplier), "lines": row.lines, "total": str(row.total),
            "revision": row.revision, "issued_at": row.issued_at.isoformat(),
            "issued_by": user.get_username(), "issued_by_id": user.pk,
        }
        row.save(update_fields=["issued_by", "issued_at", "status", "revision", "snapshot"])
        audit_order(user, "issue", row)
        return detail(row)


def register_receipt(order_id, receipt_reference):
    """Call in the transaction confirming a receipt, before writing its stock movements."""
    with transaction.atomic():
        row = PurchaseOrder.objects.select_for_update().get(pk=order_id)
        if row.status != PurchaseOrder.Status.ISSUED:
            raise Problem("purchase_order_not_issued", 409)
        link, _ = PurchaseReceiptLink.objects.get_or_create(
            receipt_reference=receipt_reference, defaults={"order": row},
        )
        if link.order_id != row.pk:
            raise Problem("receipt_reference_conflict", 409)
        return link


@endpoint("POST", module="purchase_orders")
def void(request, pk):
    user = request.user
    data = body(request)
    with transaction.atomic():
        row = locked_order(user, pk, "purchasing.po.void", data, PurchaseOrder.Status.ISSUED)
        if row.receipt_links.exists():
            raise Problem("purchase_order_has_receipts", 409)
        row.status = PurchaseOrder.Status.CANCELLED
        row.void_reason = string(data, "reason", 1000)
        row.voided_by = user
        row.voided_at = timezone.now()
        row.revision += 1
        row.save(update_fields=["status", "void_reason", "voided_by", "voided_at", "revision"])
        audit_order(user, "void", row)
        return detail(row)
