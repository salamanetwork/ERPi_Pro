from io import BytesIO
from pathlib import Path
from uuid import UUID
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from PIL import Image, ImageDraw, ImageFont
from rest_framework.response import Response
from rest_framework.exceptions import NotFound, ValidationError
from backbone.models import AuditEvent
from backbone.module_events import publish
from billing_core.models import Invoice,Receipt
from public_identity.business import context
from public_identity.security import enforce_csrf
from public_identity.views import CustomerView
from .models import Contract,CancellationRequest,Cycle

class MyContracts(CustomerView):
    def get(self,request,pk=None):
        scope,party=context(request,'subscriptions')
        if pk is not None:
            contract=get_object_or_404(Contract,pk=pk,**scope,party=party)
            cycles=Cycle.objects.filter(contract=contract).order_by('-start').values('start','end','invoice_id','invoice__number','invoice__total','invoice__paid','credit_used')
            pending=CancellationRequest.objects.filter(contract=contract,status='pending').exists()
            return Response(dict(id=pk,status=contract.status,revision=contract.revision,cancel_on=contract.cancel_on,pending_cancellation=pending,cycles=list(cycles)))
        rows=Contract.objects.filter(**scope,party=party).order_by('-pk').values('id','revision','status','next_date','cancel_on','version__plan__name_en','version__plan__name_ar')
        return Response({'results':list(rows)})

    @transaction.atomic
    def post(self,request,pk=None):
        if pk is None:raise NotFound()
        enforce_csrf(request)
        scope,party=context(request,'subscriptions',lock=True)
        contract=get_object_or_404(Contract.objects.select_for_update(),**scope,party=party,pk=pk)
        try:key=UUID(request.headers.get('Idempotency-Key',''))
        except ValueError:raise ValidationError('Invalid idempotency key.')
        reason=request.data.get('reason')
        if not isinstance(reason,str) or not 1<=len(reason.strip())<=500:raise ValidationError({'reason':'required'})
        prev=CancellationRequest.objects.filter(identity_id=request.user.pk,key=key).first()
        if prev:
            if prev.contract_id!=pk or prev.reason!=reason.strip():return Response({'error':'idempotency_conflict'},status=409)
            return Response({'id':prev.pk,'status':prev.status})
        if contract.status=='cancelled' or contract.cancel_on or request.data.get('revision')!=contract.revision or CancellationRequest.objects.filter(contract=contract,status='pending').exists():
            return Response({'error':'revision_conflict'},status=409)
        item=CancellationRequest.objects.create(contract=contract,identity_id=request.user.pk,key=key,reason=reason.strip())
        contract.revision+=1
        contract.save(update_fields=['revision'])
        AuditEvent.objects.create(actor='external:'+str(request.user.pk),action='subscriptions.cancel_requested',target=str(pk))
        publish('subscriptions','cancellation.requested',item.pk,f'subscriptions:cancel:{item.pk}')
        return Response({'id':item.pk,'status':item.status},status=201)

class Download(CustomerView):
    def get(self,request,kind,pk):
        scope,party=context(request,'billing_core')
        docs=Invoice.objects.filter(**scope,party=party).exclude(status='draft')
        ar=request.GET.get('lang')=='ar'
        if kind=='receipts':
            receipt=get_object_or_404(Receipt,**scope,invoice__in=docs,pk=pk)
            doc=receipt.invoice
            number=receipt.number
            lines=[('إيصال' if ar else 'Receipt')+' '+number,doc.number,str(receipt.amount)+' '+doc.currency]
        elif kind=='invoices':
            doc=get_object_or_404(docs,pk=pk)
            number=doc.number
            lines=[('فاتورة' if ar else 'Invoice')+' '+number]
            for row in doc.lines.order_by('pk'):
                lines.extend([row.description,f'{row.quantity} × {row.unit_price}    {row.tax_rate}%    {row.line_total} {doc.currency}'])
            for en,arabic,val in [('Subtotal','قبل الضريبة',doc.subtotal),('Tax','الضريبة',doc.tax),('Credit applied','الرصيد الدائن المستخدم',doc.applied_credit),('Total','الإجمالي',doc.total),('Paid','المدفوع',doc.paid)]:
                lines.append(f'{arabic if ar else en}   {val} {doc.currency}')
        else: raise NotFound()
        lines.insert(1,party.name_ar if ar else party.name_en)
        font=ImageFont.truetype(str(Path(__file__).with_name('fonts')/'DejaVuSans.ttf'),22)
        pages=[]
        wrapped=[]
        for line in lines:
            text=''
            for char in line:
                if len(text)>=45 and font.getlength(text+char)>722:
                    wrapped.append(text)
                    text=''
                text+=char
            wrapped.append(text)
        for i in range(0,len(wrapped),30):
            sheet=Image.new('RGB',(828,1170),'white')
            draw=ImageDraw.Draw(sheet)
            for j,line in enumerate(wrapped[i:i+30]):
                draw.text((775 if ar else 53,50+j*35),line,font=font,fill='black',anchor='rt' if ar else 'lt',direction='rtl' if ar else 'ltr')
            pages.append(sheet)
        stream=BytesIO()
        pages[0].save(stream,format='PDF',save_all=True,append_images=pages[1:],resolution=100)
        return HttpResponse(stream.getvalue(),content_type='application/pdf',headers={'Content-Disposition':f'attachment; filename="{number}.pdf"','Cache-Control':'private, no-store'})
