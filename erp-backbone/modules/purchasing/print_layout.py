from backbone.print_i18n import print_status, print_text


def _labels(language, title_key, price_key, total_key):
    return {
        "title": print_text(language, title_key),
        "site": print_text(language, "site"),
        "currency": print_text(language, "currency"),
        "description": print_text(language, "description"),
        "quantity": print_text(language, "quantity"),
        "price": print_text(language, price_key),
        "amount": print_text(language, "amount"),
        "total": print_text(language, total_key),
        "status": print_text(language, "status"),
    }


def context(row, language):
    return {
        "document": row,
        "site": row.site.name,
        "total": format(row.total, ".2f"),
        "labels": _labels(language, "purchase_requisition", "estimated_unit_price", "estimated_total"),
        "status": print_status(language, row.status, row.get_status_display()),
    }


def order_context(row, language):
    issued = row.snapshot
    supplier = issued["supplier"] if issued else {"name": row.supplier.name, "code": row.supplier.code}
    details = [
        (print_text(language, "supplier"), f"{supplier['name']} · {supplier['code']}"),
        (print_text(language, "source_requisition"), row.source_pr.document_number),
    ]
    if issued:
        for field, key in (("legal_name", "legal_name"), ("tax_id", "tax_id"), ("contact", "contact")):
            if supplier[field]:
                details.append((print_text(language, key), supplier[field]))
    return {
        "document": {**issued, "document_number": issued["number"]} if issued else row,
        "site": issued["site_name"] if issued else row.site.name,
        "total": issued["total"] if issued else format(row.total, ".2f"),
        "labels": _labels(language, "purchase_order", "unit_price", "total"),
        "status": print_status(language, row.status, row.get_status_display()),
        "details": details,
    }
