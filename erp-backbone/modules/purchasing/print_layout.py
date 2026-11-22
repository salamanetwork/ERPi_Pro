LABELS = {
    "en": ("Purchase requisition", "Site", "Currency", "Description", "Quantity",
           "Estimated unit price", "Amount", "Estimated total", "Status"),
    "ar": ("طلب شراء", "الموقع", "العملة", "الوصف", "الكمية",
           "سعر الوحدة التقديري", "القيمة", "الإجمالي التقديري", "الحالة"),
}
ARABIC_STATUSES = {"draft": "مسودة", "in_review": "قيد المراجعة", "approved": "معتمد"}


def context(row, language):
    return {
        "document": row,
        "site": row.site.name,
        "total": format(row.total, ".2f"),
        "labels": dict(zip(("title", "site", "currency", "description", "quantity", "price", "amount", "total", "status"),
                           LABELS[language], strict=True)),
        "status": ARABIC_STATUSES[row.status] if language == "ar" else row.get_status_display(),
    }


ORDER_LABELS = {
    "en": ("Purchase order", "Site", "Currency", "Description", "Quantity", "Unit price", "Amount", "Total", "Status"),
    "ar": ("أمر شراء", "الموقع", "العملة", "الوصف", "الكمية", "سعر الوحدة", "القيمة", "الإجمالي", "الحالة"),
}
ORDER_STATES = {"draft": "مسودة", "issued": "صادر", "cancelled": "ملغى"}


def order_context(row, language):
    issued = row.snapshot
    supplier = issued["supplier"] if issued else {"name": row.supplier.name, "code": row.supplier.code}
    details = [("المورد" if language == "ar" else "Supplier", f"{supplier['name']} · {supplier['code']}"),
               ("طلب الشراء" if language == "ar" else "Source requisition", row.source_pr.document_number)]
    if issued:
        for field, en, ar in (("legal_name", "Legal name", "الاسم القانوني"),
                              ("tax_id", "Tax ID", "الرقم الضريبي"), ("contact", "Contact", "التواصل")):
            if supplier[field]:
                details.append((ar if language == "ar" else en, supplier[field]))
    return {
        "document": {**issued, "document_number": issued["number"]} if issued else row,
        "site": issued["site_name"] if issued else row.site.name,
        "total": issued["total"] if issued else format(row.total, ".2f"),
        "labels": dict(zip(("title", "site", "currency", "description", "quantity", "price", "amount", "total", "status"),
                           ORDER_LABELS[language], strict=True)),
        "status": ORDER_STATES[row.status] if language == "ar" else row.get_status_display(),
        "details": details,
    }
