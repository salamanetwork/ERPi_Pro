"""In-process notifications. Publish committed identifiers, never business state or secrets."""
from django.db import transaction
from django.dispatch import Signal

customer_created = Signal()


def after_commit(signal, sender, *, using, **identifiers):
    transaction.on_commit(lambda: signal.send_robust(sender=sender, **identifiers), using=using, robust=True)
