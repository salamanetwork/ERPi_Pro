from django.db.models.signals import post_save
from django.dispatch import receiver

from backbone.events import after_commit, customer_created
from .models import Customer


@receiver(post_save, sender=Customer, dispatch_uid="sales.customer_created.v1")
def notify_new_customer(sender, instance, created, raw, using, **kwargs):
    if created and not raw:
        after_commit(customer_created, sender, using=using, customer_id=instance.pk)
