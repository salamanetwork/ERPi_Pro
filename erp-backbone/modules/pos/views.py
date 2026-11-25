from backbone.models import Profile
import json
from decimal import Decimal
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.html import escape
from django.views.decorators.cache import never_cache

from accounting.models import Account
from backbone.access import audit, has_permission, preload_scopes
from backbone.http import Problem, endpoint
from backbone.models import Site
from inventory.models import Item, Location, Position, Warehouse
from parties.models import Party
from .models import PosCategory, PosSale, PosShift, PosTerminal
from .services import checkout_sale, close_shift, compute_shift_report, open_shift, void_or_refund_sale


def _body(request):
    if not request.body:
        return {}
    return json.loads(request.body.decode("utf-8"))


def _require_perm(user, perm, site=None):
    preload_scopes(user)
    if user.is_superuser:
        return
    site_id = site.pk if hasattr(site, "pk") else site
    if not has_permission(user, perm, site_id if site_id is not None else ...):
        raise Problem("forbidden", 403)


def _serialize_terminal(t: PosTerminal):
    active_shift = t.shifts.filter(status=PosShift.STATUS_OPEN).order_by("-opened_at").first()
    return {
        "id": t.id,
        "site_id": t.site_id,
        "code": t.code,
        "name": t.name,
        "tax_rate": str(t.tax_rate),
        "is_active": t.is_active,
        "warehouse_id": t.warehouse_id,
        "location_id": t.location_id,
        "cash_account_id": t.cash_account_id,
        "card_account_id": t.card_account_id,
        "over_short_account_id": t.over_short_account_id,
        "revenue_account_id": t.revenue_account_id,
        "tax_account_id": t.tax_account_id,
        "cogs_account_id": t.cogs_account_id,
        "inventory_account_id": t.inventory_account_id,
        "open_shift": _serialize_shift(active_shift) if active_shift else None,
    }


def _serialize_shift(s: PosShift):
    return {
        "id": s.id,
        "terminal_id": s.terminal_id,
        "terminal_code": s.terminal.code,
        "terminal_name": s.terminal.name,
        "cashier": getattr(s.cashier, "username", str(s.cashier_id)),
        "opened_at": s.opened_at.isoformat(),
        "opening_balance": str(s.opening_balance),
        "closed_at": s.closed_at.isoformat() if s.closed_at else None,
        "closing_balance": str(s.closing_balance) if s.closing_balance is not None else None,
        "expected_closing": str(s.expected_closing),
        "cash_variance": str(s.cash_variance),
        "status": s.status,
    }


def _serialize_sale(sale: PosSale):
    return {
        "id": sale.id,
        "receipt_number": sale.receipt_number,
        "sale_date": sale.sale_date.isoformat(),
        "created_at": sale.created_at.isoformat(),
        "terminal_id": sale.terminal_id,
        "shift_id": sale.shift_id,
        "customer_id": sale.customer_id,
        "customer_name": (sale.customer.name_ar or sale.customer.name_en) if sale.customer_id else "Walk-in",
        "sales_document_number": sale.sales_document.number if sale.sales_document_id else None,
        "refund_document_number": sale.refund_document.number if sale.refund_document_id else None,
        "subtotal": str(sale.subtotal),
        "discount": str(sale.discount),
        "tax": str(sale.tax),
        "total": str(sale.total),
        "payment_status": sale.payment_status,
        "void_reason": sale.void_reason,
        "lines": [
            {
                "id": ln.id,
                "item_id": ln.item_id,
                "code": ln.item.code,
                "barcode": ln.item.barcode or "",
                "name": ln.item.name,
                "quantity": str(ln.quantity),
                "unit_price": str(ln.unit_price),
                "discount": str(ln.discount),
                "line_total": str(ln.line_total),
                "tax_amount": str(ln.tax_amount),
            }
            for ln in sale.lines.select_related("item").all()
        ],
        "payments": [
            {
                "id": p.id,
                "method": p.method,
                "amount": str(p.amount),
                "reference": p.reference,
            }
            for p in sale.payments.all()
        ],
    }


@never_cache
@endpoint("GET", "POST")
def terminals(request):
    if request.method == "GET":
        _require_perm(request.user, "pos.view")
        qs = PosTerminal.objects.select_related("site").filter(is_active=True).order_by("code")
        site_id = request.GET.get("site_id")
        if site_id:
            qs = qs.filter(site_id=site_id)
        return {"items": [_serialize_terminal(t) for t in qs]}

    _require_perm(request.user, "pos.admin")
    data = _body(request)
    site = get_object_or_404(Site, pk=data.get("site_id"))
    cash_acc = get_object_or_404(Account, pk=data.get("cash_account_id"))
    t = PosTerminal.objects.create(
        site=site,
        code=(data.get("code") or "").strip()[:32],
        name=(data.get("name") or "").strip()[:120],
        cash_account=cash_acc,
        card_account_id=data.get("card_account_id"),
        over_short_account_id=data.get("over_short_account_id"),
        warehouse_id=data.get("warehouse_id"),
        location_id=data.get("location_id"),
        tax_rate=Decimal(str(data.get("tax_rate", "0.0000"))),
        is_active=True,
    )
    audit(request.user, "pos.terminal.create", f"pos_terminal:{t.pk}")
    return _serialize_terminal(t)


@never_cache
@endpoint("GET")
def catalog(request):
    _require_perm(request.user, "pos.view")
    q = (request.GET.get("q") or "").strip()
    terminal_id = request.GET.get("terminal_id")
    terminal = PosTerminal.objects.filter(pk=terminal_id).first() if terminal_id else None

    qs = Item.objects.filter(active=True).order_by("name")
    if q:
        qs = qs.filter(Q(barcode__iexact=q) | Q(code__icontains=q) | Q(name__icontains=q) | Q(barcode__icontains=q))

    items = []
    for it in qs[:60]:
        stock = None
        if terminal and terminal.location_id:
            pos_row = Position.objects.filter(site=terminal.site, item=it, location_id=terminal.location_id).first()
            stock = str(pos_row.quantity) if pos_row else "0"
        items.append({
            "id": it.id,
            "code": it.code,
            "barcode": it.barcode or it.code,
            "raw_barcode": it.barcode or "",
            "name": it.name,
            "price": str(it.default_price),
            "unit_price": str(it.default_price),
            "active": it.active,
            "base_unit_id": it.base_unit_id,
            "unit_code": it.base_unit.code if it.base_unit_id else "",
            "stock": stock if stock is not None else "0",
        })
    return {"items": items}


@never_cache
@endpoint("GET", "POST")
def shifts(request):
    if request.method == "GET":
        _require_perm(request.user, "pos.view")
        qs = PosShift.objects.select_related("terminal", "cashier").order_by("-opened_at")[:50]
        return {"items": [_serialize_shift(s) for s in qs]}

    data = _body(request)
    terminal = get_object_or_404(PosTerminal, pk=data.get("terminal_id"))
    _require_perm(request.user, "pos.open_shift", terminal.site)
    shift = open_shift(
        terminal=terminal,
        cashier=request.user,
        opening_balance=data.get("opening_balance", "0.00"),
    )
    audit(request.user, "pos.shift.open", f"pos_shift:{shift.pk}")
    return _serialize_shift(shift)


@never_cache
@endpoint("POST")
def shift_close(request, pk: int):
    shift = get_object_or_404(PosShift.objects.select_related("terminal", "terminal__site"), pk=pk)
    _require_perm(request.user, "pos.close_shift", shift.terminal.site)
    data = _body(request)
    closed = close_shift(
        shift=shift,
        closing_balance=data.get("closing_balance", "0.00"),
        actor=getattr(request.user, "username", str(request.user.pk)),
        notes=data.get("notes", ""),
    )
    audit(request.user, "pos.shift.close", f"pos_shift:{closed.pk}")
    return {"shift": _serialize_shift(closed), "z_report": compute_shift_report(closed)}


@never_cache
@endpoint("GET")
def shift_report(request, pk: int):
    shift = get_object_or_404(PosShift.objects.select_related("terminal", "terminal__site", "cashier"), pk=pk)
    _require_perm(request.user, "pos.reports", shift.terminal.site)
    report_type = (request.GET.get("type") or ("Z" if shift.status == PosShift.STATUS_CLOSED else "X")).upper()
    data = compute_shift_report(shift)
    data["report_type"] = report_type
    if request.GET.get("format") == "html":
        lang = (request.GET.get("lang") or Profile.objects.filter(user=request.user).values_list("language", flat=True).first() or "en").lower()
        is_ar = lang.startswith("ar")
        if is_ar:
            html = f"""<!doctype html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8"><title>تقرير {escape(report_type)} #{shift.id}</title>
<style>body{{font-family:Noto Sans Arabic,Tahoma,sans-serif;direction:rtl;padding:24px;max-width:680px;margin:auto}}
table{{width:100%;border-collapse:collapse;margin-top:12px}}td,th{{border:1px solid #ccc;padding:8px;text-align:right}}</style>
</head><body>
<h2>تقرير وردية نقطة البيع ({escape(report_type)}) #{shift.id}</h2>
<p>الجهاز: {escape(data['terminal_name'])} ({escape(data['terminal_code'])}) | الكاشير: {escape(data['cashier'])}</p>
<table>
<tr><th>رصيد الافتتاح</th><td>{escape(data['opening_balance'])}</td></tr>
<tr><th>إجمالي المبيعات</th><td>{escape(data['total_sales'])}</td></tr>
<tr><th>الضريبة</th><td>{escape(data['tax'])}</td></tr>
<tr><th>مبيعات نقدية</th><td>{escape(data['payments']['cash'])}</td></tr>
<tr><th>مبيعات بطاقة</th><td>{escape(data['payments']['card'])}</td></tr>
<tr><th>الرصيد المتوقع بالصندوق</th><td>{escape(data['expected_closing'])}</td></tr>
<tr><th>الرصيد الفعلي عند الإغلاق</th><td>{escape(str(data['closing_balance'] or '-'))}</td></tr>
<tr><th>فارق الصندوق (زيادة/عجز)</th><td>{escape(data['cash_variance'])}</td></tr>
</table></body></html>"""
        else:
            html = f"""<!doctype html>
<html lang="en" dir="ltr"><head><meta charset="utf-8"><title>{escape(report_type)}-Report #{shift.id}</title>
<style>body{{font-family:Inter,Segoe UI,Arial,sans-serif;direction:ltr;padding:24px;max-width:680px;margin:auto}}
table{{width:100%;border-collapse:collapse;margin-top:12px}}td,th{{border:1px solid #ccc;padding:8px;text-align:left}}</style>
</head><body>
<h2>POS Shift Report ({escape(report_type)}-Report) #{shift.id}</h2>
<p>Terminal: {escape(data['terminal_name'])} ({escape(data['terminal_code'])}) | Cashier: {escape(data['cashier'])}</p>
<table>
<tr><th>Opening Balance</th><td>{escape(data['opening_balance'])}</td></tr>
<tr><th>Total Sales</th><td>{escape(data['total_sales'])}</td></tr>
<tr><th>Tax (VAT)</th><td>{escape(data['tax'])}</td></tr>
<tr><th>Cash Sales</th><td>{escape(data['payments']['cash'])}</td></tr>
<tr><th>Card Sales</th><td>{escape(data['payments']['card'])}</td></tr>
<tr><th>Expected Drawer Balance</th><td>{escape(data['expected_closing'])}</td></tr>
<tr><th>Actual Closing Balance</th><td>{escape(str(data['closing_balance'] or '-'))}</td></tr>
<tr><th>Cash Variance (Over/Short)</th><td>{escape(data['cash_variance'])}</td></tr>
</table></body></html>"""
        return HttpResponse(html, content_type="text/html; charset=utf-8")
    return data


@never_cache
@endpoint("GET", "POST")
def sales(request):
    if request.method == "GET":
        _require_perm(request.user, "pos.view")
        qs = PosSale.objects.select_related("terminal", "shift", "customer", "sales_document", "refund_document").order_by("-created_at")[:100]
        shift_id = request.GET.get("shift_id")
        if shift_id:
            qs = qs.filter(shift_id=shift_id)
        return {"items": [_serialize_sale(s) for s in qs]}

    data = _body(request)
    shift = get_object_or_404(PosShift.objects.select_related("terminal", "terminal__site"), pk=data.get("shift_id"))
    _require_perm(request.user, "pos.operate", shift.terminal.site)
    customer = Party.objects.filter(pk=data.get("customer_id")).first() if data.get("customer_id") else None
    idem_key = request.headers.get("Idempotency-Key") or data.get("idempotency_key")
    sale = checkout_sale(
        shift=shift,
        cashier=request.user,
        lines_data=data.get("lines") or [],
        payments_data=data.get("payments") or [],
        customer_party=customer,
        discount_amount=data.get("discount", "0.00"),
        idempotency_key=idem_key,
        sale_id_hint=data.get("sale_id"),
    )
    audit(request.user, "pos.sale.checkout", f"pos_sale:{sale.pk}")
    return _serialize_sale(sale)


@never_cache
@endpoint("GET")
def sale_detail(request, pk: int):
    sale = get_object_or_404(PosSale.objects.select_related("terminal", "terminal__site", "customer", "sales_document", "refund_document"), pk=pk)
    _require_perm(request.user, "pos.view", sale.terminal.site)
    return _serialize_sale(sale)


@never_cache
@endpoint("POST")
def sale_void_refund(request, pk: int):
    sale = get_object_or_404(PosSale.objects.select_related("terminal", "terminal__site"), pk=pk)
    _require_perm(request.user, "pos.refund", sale.terminal.site)
    data = _body(request)
    updated = void_or_refund_sale(sale=sale, actor_user=request.user, reason=data.get("reason", ""))
    audit(request.user, f"pos.sale.{updated.payment_status}", f"pos_sale:{updated.pk}")
    return _serialize_sale(updated)


@never_cache
@endpoint("GET")
def receipt_print(request, pk: int):
    sale = get_object_or_404(PosSale.objects.select_related("terminal", "terminal__site", "customer"), pk=pk)
    _require_perm(request.user, "pos.view", sale.terminal.site)
    rows = "".join(
        f"<tr><td>{escape(ln.item.name)}</td><td>{ln.quantity}</td><td>{ln.unit_price}</td><td>{ln.line_total}</td></tr>"
        for ln in sale.lines.select_related("item")
    )
    lang = (request.GET.get("lang") or Profile.objects.filter(user=request.user).values_list("language", flat=True).first() or "en").lower()
    is_ar = lang.startswith("ar")
    if is_ar:
        html = f"""<!doctype html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8"><title>إيصال {escape(sale.receipt_number)}</title>
<style>body{{font-family:Noto Sans Arabic,Tahoma,sans-serif;direction:rtl;width:320px;margin:auto;padding:12px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}td,th{{border-bottom:1px dashed #999;padding:4px;text-align:right}}</style>
</head><body>
<h3>إيصال مبيعات نقطة بيع</h3>
<p>رقم الإيصال: <strong>{escape(sale.receipt_number)}</strong><br>التاريخ: {escape(sale.sale_date.isoformat())}</p>
<table><thead><tr><th>الصنف</th><th>الكمية</th><th>السعر</th><th>الإجمالي</th></tr></thead><tbody>{rows}</tbody></table>
<p>المجموع الفرعي: {sale.subtotal}<br>الخصم: {sale.discount}<br>الضريبة: {sale.tax}<br><strong>الإجمالي: {sale.total}</strong></p>
</body></html>"""
    else:
        html = f"""<!doctype html>
<html lang="en" dir="ltr"><head><meta charset="utf-8"><title>Receipt {escape(sale.receipt_number)}</title>
<style>body{{font-family:Inter,Segoe UI,Arial,sans-serif;direction:ltr;width:320px;margin:auto;padding:12px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}td,th{{border-bottom:1px dashed #999;padding:4px;text-align:left}}</style>
</head><body>
<h3>POS Sales Receipt</h3>
<p>Receipt No: <strong>{escape(sale.receipt_number)}</strong><br>Date: {escape(sale.sale_date.isoformat())}</p>
<table><thead><tr><th>Item</th><th>Qty</th><th>Price</th><th>Total</th></tr></thead><tbody>{rows}</tbody></table>
<p>Subtotal: {sale.subtotal}<br>Discount: {sale.discount}<br>Tax (VAT): {sale.tax}<br><strong>Grand Total: {sale.total}</strong></p>
</body></html>"""
    return HttpResponse(html, content_type="text/html; charset=utf-8")


@never_cache
@endpoint("GET", "POST")
def admin_catalog(request):
    from inventory.models import Movement, Unit
    from django.utils import timezone

    _require_perm(request.user, "pos.view")
    terminal_id = request.GET.get("terminal_id") or (_body(request).get("terminal_id") if request.method == "POST" else None)
    terminal = PosTerminal.objects.select_related("site", "location", "warehouse").filter(pk=terminal_id).first()

    if request.method == "GET":
        q = (request.GET.get("q") or "").strip()
        qs = Item.objects.select_related("base_unit").order_by("code")
        if q:
            qs = qs.filter(Q(code__icontains=q) | Q(name__icontains=q) | Q(barcode__icontains=q))
        rows = []
        for it in qs[:200]:
            stock = "0"
            if terminal and terminal.location_id:
                pos_row = Position.objects.filter(site=terminal.site, item=it, location_id=terminal.location_id).first()
                stock = str(pos_row.quantity) if pos_row else "0"
            rows.append({
                "id": it.id,
                "code": it.code,
                "name": it.name,
                "barcode": it.barcode or "",
                "default_price": f"{it.default_price:.2f}",
                "active": it.active,
                "base_unit_id": it.base_unit_id,
                "unit_code": it.base_unit.code,
                "stock": stock,
            })
        return {
            "items": rows,
            "units": list(Unit.objects.order_by("code").values("id", "code", "name")),
            "sites": list(Site.objects.filter(active=True).order_by("name").values("id", "name")),
            "warehouses": list(Warehouse.objects.filter(active=True).order_by("code").values("id", "code", "name", "site_id")),
            "locations": list(Location.objects.order_by("code").values("id", "code", "name", "warehouse_id")),
            "accounts": list(Account.objects.filter(active=True).order_by("code").values("id", "code", "name")),
        }

    _require_perm(request.user, "inventory.setup", None)
    data = _body(request)
    action = data.get("action") or "save_item"

    if action == "save_item":
        item_id = data.get("id")
        code = (data.get("code") or "").strip().upper()[:32]
        name = (data.get("name") or "").strip()[:120]
        barcode = (data.get("barcode") or "").strip()[:64]
        price = Decimal(str(data.get("default_price") or "0.00"))
        active = bool(data.get("active", True))
        if not name or price < 0:
            raise Problem("invalid_item_payload", 422)
        if item_id:
            it = get_object_or_404(Item, pk=item_id)
            if barcode and Item.objects.filter(barcode=barcode).exclude(pk=it.pk).exists():
                raise Problem("inventory_barcode_exists", 409)
            it.name = name
            it.barcode = barcode
            it.default_price = price
            it.active = active
            it.revision += 1
            it.save(update_fields=["name", "barcode", "default_price", "active", "revision"])
            audit(request.user, "pos.catalog.item_update", it.code)
        else:
            if not code:
                raise Problem("invalid_code", 422)
            if Item.objects.filter(code=code).exists():
                raise Problem("inventory_code_exists", 409)
            if barcode and Item.objects.filter(barcode=barcode).exists():
                raise Problem("inventory_barcode_exists", 409)
            unit = Unit.objects.filter(pk=data.get("base_unit_id")).first() or Unit.objects.order_by("id").first()
            if not unit:
                raise Problem("inventory_unit_missing", 409)
            it = Item.objects.create(code=code, name=name, base_unit=unit, barcode=barcode, default_price=price, active=active)
            audit(request.user, "pos.catalog.item_create", it.code)
        return {"id": it.id, "code": it.code, "name": it.name, "barcode": it.barcode, "default_price": f"{it.default_price:.2f}", "active": it.active}

    if action == "adjust_stock":
        if not terminal or not terminal.location_id:
            raise Problem("pos_terminal_location_required", 409)
        it = get_object_or_404(Item, pk=data.get("item_id"))
        delta = Decimal(str(data.get("delta") or "0"))
        if delta == 0:
            raise Problem("invalid_quantity", 422)
        pos_row, _ = Position.objects.get_or_create(
            site=terminal.site,
            item=it,
            location_id=terminal.location_id,
            defaults={"quantity": Decimal("0")},
        )
        new_qty = pos_row.quantity + delta
        if new_qty < 0:
            raise Problem("insufficient_stock", 409)
        pos_row.quantity = new_qty
        pos_row.save(update_fields=["quantity"])
        Movement.objects.create(
            site=terminal.site,
            item=it,
            location_id=terminal.location_id,
            quantity=delta,
            value=Decimal("0.00"),
            kind="adjustment",
            occurred_at=timezone.now(),
            business_date=timezone.localdate(),
            actor=request.user.get_username(),
            actor_id=request.user.pk,
            facts={"terminal": terminal.code, "source": "pos_catalog"},
        )
        audit(request.user, "pos.catalog.stock_adjust", f"{it.code}:{delta}")
        return {"item_id": it.id, "stock": str(pos_row.quantity)}

    raise Problem("invalid_action", 400)


@never_cache
@endpoint("PATCH")
def terminal_update(request, pk: int):
    _require_perm(request.user, "pos.admin")
    t = get_object_or_404(PosTerminal, pk=pk)
    data = _body(request)
    if "name" in data and str(data["name"]).strip():
        t.name = str(data["name"]).strip()[:120]
    if "tax_rate" in data and data["tax_rate"] not in (None, ""):
        t.tax_rate = Decimal(str(data["tax_rate"]))
    if "warehouse_id" in data and data["warehouse_id"]:
        t.warehouse_id = int(data["warehouse_id"])
    if "location_id" in data and data["location_id"]:
        t.location_id = int(data["location_id"])
    if "is_active" in data:
        t.is_active = bool(data["is_active"])
    t.save()
    audit(request.user, "pos.terminal.update", f"pos_terminal:{t.pk}")
    return _serialize_terminal(t)
