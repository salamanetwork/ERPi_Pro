from decimal import Decimal,ROUND_HALF_UP,InvalidOperation
from django.core.exceptions import ValidationError
from django.core.validators import DecimalValidator
from django.shortcuts import render
from backbone.business_scope import one
from backbone.models import Site
from backbone.http import string
from backbone.validation import Problem
from backbone.module_events import publish
from parties.models import Party
from scheduling.models import Appointment
from .models import Counter,Invoice,Line

def amount(data,key,digits,places):
    try:
        raw=data[key]
        if not isinstance(raw,(int,str)) or isinstance(raw,bool) or len(str(raw))>40:raise ValueError
        value=Decimal(raw)
        if not value.is_finite() or value<0:raise ValueError
        DecimalValidator(digits,places)(value)
        return value
    except (ValueError,KeyError,ValidationError,InvalidOperation):raise Problem('invalid_amount',field=key)
def money(x):return x.quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
def number(scope,kind):
    Site.objects.select_for_update().filter(active=True).get(pk=scope['site_id'])
    seq,_=Counter.objects.select_for_update().get_or_create(**scope,kind=kind)
    seq.last+=1
    seq.save(update_fields=['last'])
    return f'{kind}-{seq.last:06d}'
def draft(data,scope,*,applied_credit=Decimal(0)):
    party=one(Party.objects.filter(**scope,active=True),data.get('party_id'))
    appt=one(Appointment.objects.filter(**scope,party=party),data['appointment_id']) if data.get('appointment_id') else None
    currency=string(data,'currency',3).upper()
    if len(currency)!=3 or not currency.isascii() or not currency.isalpha():raise Problem('invalid_currency',field='currency')
    rows=data.get('lines')
    if not isinstance(rows,list) or not 1<=len(rows)<=200:raise Problem('invalid_lines',field='lines')
    subtotal=tax=Decimal('0')
    lines=[]
    for i,row in enumerate(rows):
        if not isinstance(row,dict):raise Problem('invalid_line',field=f'lines.{i}')
        desc=string(row,'description',240)
        qty=amount(row,'quantity',12,3)
        price=amount(row,'unit_price',14,4)
        rate=amount(row,'tax_rate',6,3)
        if qty<=0 or rate>100:raise Problem('invalid_line',field=f'lines.{i}')
        net=money(qty*price)
        vat=money(net*rate/100)
        subtotal+=net
        tax+=vat
        if subtotal+tax>=10**16:raise Problem('total_too_large',field='lines')
        lines.append(Line(description=desc,quantity=qty,unit_price=price,tax_rate=rate,line_total=net+vat))
    if applied_credit<0 or subtotal+tax-applied_credit<=0:raise Problem('invalid_total',field='lines')
    doc=Invoice.objects.create(**scope,party=party,appointment=appt,currency=currency,number=number(scope,'INV'),subtotal=subtotal,tax=tax,applied_credit=applied_credit,total=subtotal+tax-applied_credit)
    for line in lines:line.invoice=doc
    Line.objects.bulk_create(lines)
    return doc,'invoice.created'

def issue(doc):
    from django.db import transaction
    with transaction.atomic():
        locked = Invoice.objects.select_for_update().get(pk=doc.pk)
        if locked.status != 'draft':
            raise Problem('invalid_transition', 409)
        locked.status = 'issued'
        locked.revision += 1
        locked.save(update_fields=['status', 'revision'])
        doc.status = locked.status
        doc.revision = locked.revision
        publish('billing_core', 'invoice.issued', locked.pk, f'billing_core:invoice:{locked.pk}:issued')
