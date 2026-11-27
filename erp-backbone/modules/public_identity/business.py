from datetime import datetime, date, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo
from django.db import transaction
from django.shortcuts import get_object_or_404, render
from django.utils.timezone import now
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from backbone.registry import enabled_modules
from backbone.module_events import publish
from backbone.validation import Problem
from parties.models import Party
from scheduling.models import Appointment, Resource, PortalBookingRequest
from scheduling.views import book
from billing_core.models import Invoice, Receipt
from .views import CustomerView
from .security import enforce_csrf
from .models import ExternalIdentity


def context(request,module,lock=False):
    if not {'parties',module}.issubset(enabled_modules()): raise NotFound()
    scope={'site_id':request.auth.space.internal_site_id,'space_id':request.auth.space_id}
    identity=request.user.identity
    rows=Party.objects
    if lock:
        rows=rows.select_for_update()
    party=get_object_or_404(rows,pk=identity.party_id,active=True,**scope)
    if lock and not ExternalIdentity.objects.filter(pk=identity.pk,party=party).exists(): raise NotFound()
    return scope,party


class Records(CustomerView):
    def get(self,request,kind):
        scope,party=context(request,'scheduling' if kind in ('appointments','resources') else 'billing_core')
        if kind=='appointments':
            rows=Appointment.objects.filter(**scope,party=party).values('id','resource__name','start','status')
        elif kind=='resources':
            rows=Resource.objects.filter(**scope).values('id','name','timezone')
        elif kind=='invoices':
            rows=Invoice.objects.filter(**scope,party=party).exclude(status='draft').values('id','number','currency','total','status')
        elif kind=='receipts':
            rows=Receipt.objects.filter(**scope,invoice__party=party,invoice__site_id=party.site_id,invoice__space_id=party.space_id).values('id','number','amount','invoice__currency')
        else: raise NotFound()
        return Response({'results':list(rows.order_by('-pk'))})

    @transaction.atomic
    def post(self,request,kind):
        if kind!='appointments': raise NotFound()
        enforce_csrf(request)
        scope,party=context(request,'scheduling',lock=True)
        try:
            key=UUID(request.headers.get('Idempotency-Key',''))
            start=datetime.fromisoformat(request.data['start'])
            resource=request.data['resource_id']
            if not start.tzinfo or type(resource)!=int: raise ValueError()
        except (KeyError,ValueError,TypeError):
            raise ValidationError('Invalid booking.')
        old=PortalBookingRequest.objects.filter(identity_id=request.user.pk,key=key).select_related('appointment').first()
        if old:
            ap=old.appointment
            if (ap.party_id,ap.space_id,ap.resource_id,ap.start)!=(party.pk,party.space_id,resource,start):
                return Response({'error':'idempotency_conflict'},status=409)
            return Response({'id':ap.pk})
        if start<=now():raise ValidationError('Select a future slot.')
        try:
            ap,event=book(dict(start=start.isoformat(),resource_id=resource,party_id=party.pk),scope)
        except Problem as exc:
            return Response(exc.response(),status=exc.status)
        PortalBookingRequest.objects.create(identity_id=request.user.pk,appointment=ap,key=key)
        publish('scheduling',event,ap.pk,f'scheduling:{event}:{ap.pk}:{ap.revision}')
        return Response({'id':ap.pk},status=201)


class Slots(CustomerView):
    def get(self,request,pk):
        scope,_=context(request,'scheduling')
        res=get_object_or_404(Resource,pk=pk,**scope)
        try:
            day=date.fromisoformat(request.query_params.get('date',''))
            if not 1<day.year<9999:raise ValueError
        except ValueError:raise ValidationError('Invalid date.')
        zone=ZoneInfo(res.timezone)
        start=datetime.combine(day,res.opens,zone).astimezone(timezone.utc)
        limit=datetime.combine(day,res.closes,zone).astimezone(timezone.utc)
        duration=timedelta(minutes=res.slot_minutes)
        busy=list(Appointment.objects.filter(resource=res,start__lt=limit,end__gt=start).exclude(status='cancelled').values_list('start','end'))
        slots=[]
        while duration>timedelta(0) and day.weekday() in res.weekdays and start+duration<=limit:
            end=start+duration
            local=start.astimezone(zone)
            offset=local.hour*60+local.minute-res.opens.hour*60-res.opens.minute
            if start>now() and offset>=0 and offset%res.slot_minutes==0 and local.utcoffset()==end.astimezone(zone).utcoffset() and not any(a<end and b>start for a,b in busy):
                slots.append(start.isoformat())
            start=end
        return Response({'results':slots})


class PrintDocument(CustomerView):
    def get(self,request,kind,pk):
        scope,party=context(request,'billing_core')
        docs=Invoice.objects.filter(**scope,party=party).exclude(status='draft')
        receipt=None
        if kind=='receipts':
            receipt=get_object_or_404(Receipt,invoice__in=docs,pk=pk,**scope)
            doc=receipt.invoice
        elif kind=='invoices':doc=get_object_or_404(docs,pk=pk)
        else:raise NotFound()
        from backbone.company_profile import branding
        from backbone.print_i18n import is_rtl_language, normalize_print_language, print_direction, print_strings
        lang = normalize_print_language(request.GET.get('lang') or request.GET.get('language'), 'en')
        return render(request,'billing_core/document.html',dict(doc=doc,receipt=receipt,language=lang,dir=print_direction(lang),ui=print_strings(lang),ar=is_rtl_language(lang),company=branding()))
