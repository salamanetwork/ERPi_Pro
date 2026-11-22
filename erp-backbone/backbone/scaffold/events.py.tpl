from .models import EventReceipt


def handle_event(event):
    # The receipt and local projections commit with dispatcher acknowledgement.
    EventReceipt.objects.get_or_create(event=event)
