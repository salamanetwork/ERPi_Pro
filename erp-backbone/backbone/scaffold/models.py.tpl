from django.db import models


class Record(models.Model):
    id = models.BigAutoField(primary_key=True)
    name = models.CharField(max_length=160)
    __SCOPE_FIELD__


class EventReceipt(models.Model):
    event = models.OneToOneField("backbone.OutboxEvent",on_delete=models.PROTECT,related_name="+")
    received_at = models.DateTimeField(auto_now_add=True)
