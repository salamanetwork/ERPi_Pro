import base64
import binascii
import re

from django.db import transaction
from django.shortcuts import render

from backbone.access import audit
from backbone.company_profile import branding
from backbone.http import Problem, body, endpoint, string
from backbone.models import PrintTemplate
from .documents import definition, scoped_document


def template(kind, language):
    if language not in ("ar", "en"):
        raise Problem("invalid_language")
    if definition(kind).print_context is None:
        raise Problem("unknown_document_type", 404)
    return PrintTemplate.objects.filter(kind=kind, language=language).first()


def serialize(row):
    return {"heading": row.heading, "company_name": row.company_name, "footer": row.footer,
            "logo": row.logo, "revision": row.revision} if row else {
        "heading": "", "company_name": "", "footer": "", "logo": "", "revision": 0,
    }


def logo_value(data):
    logo = string(data, "logo", 350_000, required=False)
    if not logo:
        return ""
    match = re.fullmatch(r"data:image/(png|jpeg);base64,([A-Za-z0-9+/]+={0,2})", logo)
    if match is None:
        raise Problem("invalid_print_logo")
    try:
        image = base64.b64decode(match[2], validate=True)
    except binascii.Error:
        raise Problem("invalid_print_logo") from None
    magic = b"\x89PNG\r\n\x1a\n" if match[1] == "png" else b"\xff\xd8\xff"
    if not image.startswith(magic) or len(image) > 256 * 1024:
        raise Problem("invalid_print_logo")
    return logo


@endpoint("GET", "PATCH", admin=True)
def print_template(request, kind, language):
    current = template(kind, language)
    if request.method == "GET":
        return serialize(current)
    data = body(request)
    revision = data.get("revision")
    if type(revision) is not int or revision < 0:
        raise Problem("invalid_revision")
    with transaction.atomic():
        row, created = PrintTemplate.objects.select_for_update().get_or_create(kind=kind, language=language)
        if revision != (0 if created else row.revision):
            raise Problem("revision_conflict", 409)
        row.heading = string(data, "heading", 120, required=False)
        row.company_name = string(data, "company_name", 120, required=False)
        row.footer = string(data, "footer", 240, required=False)
        row.logo = logo_value(data)
        row.revision = revision + 1
        row.save()
        audit(request.user, "print.configure", f"{kind.upper()} / {language}")
        return serialize(row)


@endpoint("GET")
def print_document(request, kind, pk):
    document, row = scoped_document(request.user, kind, pk)
    language = request.GET.get("language", "en")
    settings = serialize(template(kind, language))
    response = render(request, "backbone/document_print.html", {
        **document.print_context(row, language), "language": language, "layout": settings, "company": branding(),
    })
    response["Cache-Control"] = "private, no-store"
    response["Content-Security-Policy"] = "default-src 'none'; img-src data:; font-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"
    return response


@endpoint("POST", admin=True)
def preview(request, kind, language):
    template(kind, language)
    data = body(request)
    layout = {field: string(data, field, size, required=False)
              for field, size in (("heading", 120), ("company_name", 120), ("footer", 240))}
    layout["logo"] = logo_value(data)
    arabic = language == "ar"
    response = render(request, "backbone/document_print.html", {
        "document": {"document_number": f"{kind.upper()}-000001", "title": "معاينة" if arabic else "Preview",
                     "currency": "AED", "lines": [{"description": "بند تجريبي" if arabic else "Sample item",
                                                   "quantity": 1, "unit_price": "125.00", "amount": "125.00"}]},
        "language": language, "layout": layout, "company": branding(), "site": "—", "status": "—", "total": "125.00",
        "labels": dict(zip(("title", "site", "status", "currency", "description", "quantity", "price", "amount", "total"),
                           ("مستند تجريبي", "الموقع", "الحالة", "العملة", "الوصف", "الكمية", "السعر", "المبلغ", "الإجمالي")
                           if arabic else ("Sample document", "Site", "Status", "Currency", "Description",
                                           "Quantity", "Price", "Amount", "Total"))),
    })
    response["Cache-Control"] = "private, no-store"
    response["X-Frame-Options"] = "SAMEORIGIN"
    response["Content-Security-Policy"] = "default-src 'none'; img-src data:; font-src 'self'; style-src 'unsafe-inline'; base-uri 'none'"
    return response
