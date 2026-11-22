"""Expected internal shipments available to destination planning."""
from decimal import Decimal

from .models import ShipmentLine


def incoming_shipments(site_id):
    rows = ShipmentLine.objects.filter(shipment__transfer__destination_id=site_id,remaining__gt=0).select_related(
        "shipment","line").prefetch_related("discrepancies")
    for row in rows.iterator(chunk_size=100):
        pending = sum((report.quantity for report in row.discrepancies.all() if report.state=="pending"),Decimal(0))
        if row.remaining > pending:
            yield row.line.item_id, row.shipment.eta, row.remaining-pending
