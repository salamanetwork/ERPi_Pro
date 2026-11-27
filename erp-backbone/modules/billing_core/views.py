from decimal import Decimal,ROUND_HALF_UP,InvalidOperation
from django.core.exceptions import ValidationError
from django.core.validators import DecimalValidator
from django.shortcuts import render
from backbone.business_api import collection
from backbone.business_scope import select_scope,one,revision
from backbone.models import Site
from backbone.http import string,endpoint,once,body
from backbone.validation import Problem
from backbone.access import audit
from backbone.module_events import publish
from parties.models import Party
from scheduling.models import Appointment
from .models import Counter,Invoice,Line,Receipt

from .services import amount, money, number, draft, issue
invoices=collection('billing_core',Invoice,[f.attname for f in Invoice._meta.fields]+['party__name_en','party__name_ar'],['number','status','party__name_en','party__name_ar'],draft)

@endpoint('POST',module='billing_core')
def action(request,pk,action):
    if action not in ('issue','void','pay'):raise Problem('not_found',404)
    data=body(request)
    scope=select_scope(request,data,'billing_core.'+('pay' if action=='pay' else 'issue'))
    def change():
        if not Site.objects.select_for_update().filter(pk=scope['site_id'],active=True).exists():
            raise Problem('inactive_site',409)
        doc=one(Invoice.objects.select_for_update().filter(**scope),pk)
        revision(doc,data)
        receipt=None
        if action=='pay':
            if doc.status not in ('issued','partially_paid'):raise Problem('invalid_transition',409)
            paid=amount(data,'amount',18,2)
            if not 0<paid<=doc.total-doc.paid:raise Problem('invalid_amount',field='amount')
            receipt=Receipt.objects.create(**scope,invoice=doc,amount=paid,number=number(scope,'RCT'))
            doc.paid+=paid
            doc.status='paid' if doc.paid==doc.total else 'partially_paid'
            publish('billing_core','receipt.recorded',receipt.id,'billing_core:receipt:'+str(receipt.pk))
        elif action=='issue':
            if not doc.party.active:raise Problem('inactive_party',409)
            issue(doc)
        elif action=='void' and doc.status in ('draft','issued'):
            from subscriptions.gl import protect_void
            protect_void(doc)
            doc.status='void'
        else:raise Problem('invalid_transition',409)
        doc.save()
        if action!='issue' and doc.status!='partially_paid':publish('billing_core','invoice.'+doc.status,doc.pk,'billing_core:invoice:'+str(doc.pk)+':'+doc.status)
        audit(request.user,'billing_core.'+action,doc.pk)
        return dict(id=doc.pk,revision=doc.revision,status=doc.status,receipt=receipt.pk if receipt else None)
    return once(request,f'billing_core.{pk}.{action}',data,change)

@endpoint('GET',module='billing_core')
def printable(request,kind,pk):
    scope=select_scope(request,request.GET,'billing_core.view')
    receipt=None
    if kind=='receipts':
        receipt=one(Receipt.objects.filter(**scope),pk)
        doc=receipt.invoice
    elif kind=='invoices':doc=one(Invoice.objects.filter(**scope),pk)
    else:raise Problem('not_found',404)
    from backbone.company_profile import branding
    return render(request,'billing_core/document.html',dict(doc=doc,receipt=receipt,ar=request.GET.get('lang')=='ar',company=branding()))

@endpoint('GET',module='billing_core')
def receipts(request):
    from backbone.business_scope import page
    scope=select_scope(request,request.GET,'billing_core.view')
    return page(request.GET,Receipt.objects.filter(**scope).order_by('-pk').values('id','number','amount','invoice__number','invoice__currency','created_at'),['number','invoice__number'])
