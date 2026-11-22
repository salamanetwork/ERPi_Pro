from decimal import Decimal

from .models import Item


def receipt_context(row, language):
    ar = language == "ar"
    labels = ("إذن استلام", "الموقع", "العملة", "الوصف", "الكمية", "سعر الوحدة", "القيمة", "الإجمالي", "الحالة") if ar else (
        "Goods receipt", "Site", "Currency", "Description", "Quantity", "Unit price", "Amount", "Total", "Status")
    items = Item.objects.in_bulk([line["item_id"] for line in row.lines])
    lines = [{**line, "description": f'{items[line["item_id"]].code} · {line["description"]}'} for line in row.lines]
    details = [("أمر الشراء" if ar else "Purchase order", row.order.number),
               ("المخزن" if ar else "Warehouse", row.warehouse.name)]
    if row.supplier_reference:
        details.append(("مرجع المورد" if ar else "Supplier reference", row.supplier_reference))
    return {
        "document": {"document_number": row.number, "title": row.order.title, "currency": "AED", "lines": lines},
        "site": row.site.name, "total": (str(sum(Decimal(line["amount"]) for line in lines))
                                       if row.status == "confirmed" else "—"),
        "status": ({"draft": "مسودة", "confirmed": "مؤكد"}[row.status] if ar else row.status.title()),
        "labels": dict(zip(("title", "site", "currency", "description", "quantity", "price", "amount", "total", "status"),
                           labels, strict=True)),
        "details": details,
    }
