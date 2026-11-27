from decimal import Decimal

from backbone.print_i18n import print_status, print_text
from .models import Item


def receipt_context(row, language):
    labels = {
        "title": print_text(language, "goods_receipt"),
        "site": print_text(language, "site"),
        "currency": print_text(language, "currency"),
        "description": print_text(language, "description"),
        "quantity": print_text(language, "quantity"),
        "price": print_text(language, "unit_price"),
        "amount": print_text(language, "amount"),
        "total": print_text(language, "total"),
        "status": print_text(language, "status"),
    }
    items = Item.objects.in_bulk([line["item_id"] for line in row.lines])
    lines = [{**line, "description": f'{items[line["item_id"]].code} · {line["description"]}'} for line in row.lines]
    details = [
        (print_text(language, "purchase_order"), row.order.number),
        (print_text(language, "warehouse"), row.warehouse.name),
    ]
    if row.supplier_reference:
        details.append((print_text(language, "supplier_reference"), row.supplier_reference))
    return {
        "document": {"document_number": row.number, "title": row.order.title, "currency": "AED", "lines": lines},
        "site": row.site.name,
        "total": (str(sum(Decimal(line["amount"]) for line in lines)) if row.status == "confirmed" else "—"),
        "status": print_status(language, row.status, row.status.title()),
        "labels": labels,
        "details": details,
    }
