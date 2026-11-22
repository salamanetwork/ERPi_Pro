from django.db import models
from backbone.business_scope import ScopedRecord

class Party(ScopedRecord):
    party_type=models.CharField(max_length=10)
    name_en=models.CharField(max_length=180)
    name_ar=models.CharField(max_length=180)
    phone=models.CharField(max_length=40,blank=True)
    email=models.EmailField(blank=True)
    identity_number=models.CharField(max_length=80,blank=True)
    address=models.TextField(blank=True)
    notes=models.TextField(blank=True)
    active=models.BooleanField(default=True)
    account=models.ForeignKey('public_identity.ExternalIdentity',related_name='linked_parties',on_delete=models.PROTECT,null=True,blank=True)
