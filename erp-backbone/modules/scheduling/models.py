from django.db import models
from backbone.business_scope import ScopedRecord

class Resource(ScopedRecord):
    name=models.CharField(max_length=180)
    timezone=models.CharField(max_length=80)
    weekdays=models.JSONField()
    opens=models.TimeField()
    closes=models.TimeField()
    slot_minutes=models.PositiveSmallIntegerField()

class Appointment(ScopedRecord):
    resource=models.ForeignKey(Resource,on_delete=models.PROTECT)
    party=models.ForeignKey('parties.Party',on_delete=models.PROTECT)
    start=models.DateTimeField()
    end=models.DateTimeField()
    status=models.CharField(max_length=12,default='scheduled')
    class Meta:
        indexes=[models.Index(fields=['resource','start','end'])]

class PortalBookingRequest(models.Model):
    identity = models.ForeignKey('public_identity.ExternalIdentity',on_delete=models.PROTECT)
    appointment = models.ForeignKey(Appointment,on_delete=models.PROTECT)
    key = models.UUIDField()
    class Meta:
        constraints=[models.UniqueConstraint(fields=['identity','key'],name='unique_portal_booking_key')]
