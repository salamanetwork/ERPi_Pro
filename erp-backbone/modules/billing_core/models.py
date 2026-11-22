from django.db import models
from backbone.business_scope import ScopedRecord
from decimal import Decimal

class Counter(ScopedRecord):
    kind=models.CharField(max_length=8)
    last=models.PositiveIntegerField(default=0)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['site','kind'],condition=models.Q(space=None),name='billing_counter_site'),
                     models.UniqueConstraint(fields=['site','space','kind'],condition=models.Q(space__isnull=False),name='billing_counter_space')]

class Invoice(ScopedRecord):
    number=models.CharField(max_length=30)
    party=models.ForeignKey('parties.Party',on_delete=models.PROTECT)
    appointment=models.ForeignKey('scheduling.Appointment',on_delete=models.PROTECT,null=True)
    currency=models.CharField(max_length=3)
    status=models.CharField(max_length=16,default='draft')
    subtotal=models.DecimalField(max_digits=18,decimal_places=2)
    tax=models.DecimalField(max_digits=18,decimal_places=2)
    total=models.DecimalField(max_digits=18,decimal_places=2)
    applied_credit=models.DecimalField(max_digits=18,decimal_places=2,default=Decimal('0'))
    paid=models.DecimalField(max_digits=18,decimal_places=2,default=Decimal('0'))
class Line(models.Model):
    invoice=models.ForeignKey(Invoice,on_delete=models.PROTECT,related_name='lines')
    description=models.CharField(max_length=240)
    quantity=models.DecimalField(max_digits=12,decimal_places=3)
    unit_price=models.DecimalField(max_digits=14,decimal_places=4)
    tax_rate=models.DecimalField(max_digits=6,decimal_places=3)
    line_total=models.DecimalField(max_digits=18,decimal_places=2)
class Receipt(ScopedRecord):
    invoice=models.ForeignKey(Invoice,on_delete=models.PROTECT)
    number=models.CharField(max_length=30)
    amount=models.DecimalField(max_digits=18,decimal_places=2)
    created_at=models.DateTimeField(auto_now_add=True)
