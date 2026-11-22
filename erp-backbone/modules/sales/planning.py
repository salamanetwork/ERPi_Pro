"""Site-scoped planning inputs owned by Sales."""
from decimal import Decimal

from .models import Document
from .views import order_progress


def open_requirements(site_id):
    for order in Document.objects.filter(site_id=site_id, kind="order", status="confirmed").iterator():
        progress = {line["line_id"]: Decimal(line["remaining"]) for line in order_progress(order)}
        for line in order.lines:
            yield (line["item_id"], order.due or order.date, progress[line["id"]], order.number, f"sales:{order.pk}")
