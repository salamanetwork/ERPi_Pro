from .models import Order


def open_orders(site_id=None):
    rows = Order.objects.filter(state="released")
    return rows if site_id is None else rows.filter(site_id=site_id)
