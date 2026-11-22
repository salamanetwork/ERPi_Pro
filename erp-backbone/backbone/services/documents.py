from dataclasses import dataclass
from collections.abc import Callable

from backbone.access import visible
from backbone.http import Problem, require
from backbone.models import ArchivedDocument
from backbone.registry import enabled_modules


@dataclass(frozen=True)
class Document:
    module: str
    model: type
    route: str
    default_prefix: str
    minimum_start: Callable
    print_context: Callable | None = None
    can_review: Callable | None = None
    allow_site_numbering: bool = True
    view_permission: str | None = None
    edit_permission: str | None = None


def catalog():
    from purchasing.print_layout import context as pr_print, order_context
    from purchasing.models import PurchaseOrder, Requisition
    from purchasing.views import authority
    from django.db.models import Max
    from accounting.models import SupplierInvoice
    from inventory.models import Receipt
    from inventory.printing import receipt_context

    def pr_start():
        legacy_max = Requisition.objects.filter(number__isnull=True).aggregate(maximum=Max("pk"))["maximum"]
        return (legacy_max or 0) + 1

    def can_review_pr(user, row, task, scopes):
        run = row.reviews.filter(status="pending").first()
        if run is None or row.status != "in_review":
            return False
        return (task.step_key == f"{run.pk}:{run.next_step}"
                and task.role_id == run.steps[run.next_step]["id"]
                and authority(user, row, run, scopes)[0])

    return {
        "si": Document("supplier_invoices", SupplierInvoice, "/payables/invoices", "SI", lambda: 1,
                       view_permission="ap.view", edit_permission="ap.create"),
        "pr": Document("purchasing", Requisition, "/purchase-requests", "PR", pr_start,
                       print_context=pr_print, can_review=can_review_pr),
        "grn": Document("inventory", Receipt, "/inventory/receipts", "GRN", lambda: 1,
                        print_context=receipt_context, edit_permission="inventory.receive"),
        "po": Document("purchase_orders", PurchaseOrder, "/purchase-orders", "PO", lambda: 1,
                       print_context=order_context, allow_site_numbering=False,
                       view_permission="purchasing.view", edit_permission="purchasing.po.create"),
    }


def definition(kind):
    document = catalog().get(kind)
    if document is None:
        raise Problem("unknown_document_type", 404)
    return document


def scoped_document(user, kind, object_id, *, lock=False):
    document = definition(kind)
    if document.module not in enabled_modules():
        raise Problem("module_disabled", 404)
    rows = document.model.objects.all()
    if lock:
        rows = rows.select_for_update()
    row = visible(rows, user, document.view_permission or f"{document.module}.view").filter(pk=object_id).first()
    if row is None:
        raise Problem("not_found", 404)
    return document, row


def require_draft_edit(user, document, row):
    require(user, document.edit_permission or f"{document.module}.edit", row.site_id)
    if row.status != "draft":
        raise Problem("purchase_draft_required", 409)


def archive_rows(kind):
    return ArchivedDocument.objects.filter(kind=kind)


def reject_archived(kind, row):
    if archive_rows(kind).filter(object_id=row.pk).exists():
        raise Problem("document_archived", 409)
