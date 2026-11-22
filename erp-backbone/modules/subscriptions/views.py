from datetime import date, timedelta
from django.db import IntegrityError
from accounting.models import Ledger
from backbone.access import audit
from backbone.business_api import choice
from backbone.business_scope import one, page, revision, select_scope
from backbone.http import body, endpoint, once, string
from backbone.validation import Problem
from billing_core.services import amount
from inventory.models import SiteSettings
from parties.models import Party
from . import engine
from .models import CancellationRequest, Contract, Cycle, DunningPolicy, Notice, Plan, PlanVersion

MODULE='subscriptions'

def integer(data, key, low, high, default):
    value=data.get(key,default)
    if type(value)!=int or not low<=value<=high:
        raise Problem('invalid_number',field=key)
    return value

def version(plan,data):
    ledger=Ledger.objects.filter(pk=1,activated_at__isnull=False).first()
    if not ledger:
        raise Problem('ledger_inactive',409)
    interval=choice(data,'interval',['monthly','quarterly','annual','custom'])
    price=amount(data,'price',14,2)
    tax=amount(data,'tax',6,3)
    if price<=0 or tax>100:
        raise Problem('invalid_amount',field='price' if price<=0 else 'tax')
    return PlanVersion.objects.create(plan=plan,version=plan.revision,currency=ledger.currency,
        interval=interval,days=integer(data,'days',1,3650,30) if interval=='custom' else 1,
        price=price,tax=tax,setup=amount(data,'setup',14,2),trial_days=integer(data,'trial_days',0,3650,0))

@endpoint('GET','POST',module=MODULE)
def collection(request,kind):
    if kind not in ('contracts','plans','versions','cycles','requests','notices','policy','parties','spaces'):
        raise Problem('not_found',404)
    data=request.GET if request.method=='GET' else body(request)
    perm='view' if request.method=='GET' else 'override' if kind=='policy' else 'manage'
    scope=select_scope(request,data,'subscriptions.'+perm)
    if request.method=='GET':
        if kind=='spaces':
            from publication.models import PublicationSpace
            rows=PublicationSpace.objects.filter(active=True,internal_site_id=scope['site_id']).values('id','name')
        elif kind=='policy':
            p=DunningPolicy.objects.filter(site_id=scope['site_id']).first() or DunningPolicy(revision=0)
            return {k:getattr(p,k) for k in ('revision','first','second','final','suspend')}
        elif kind=='parties':
            rows=Party.objects.filter(**scope,active=True).values('id','name_en','name_ar')
        elif kind=='contracts':
            rows=Contract.objects.filter(**scope).values('id','revision','status','party__name_en','party__name_ar','version__plan__code','next_date','cancel_on','zone')
        elif kind=='plans':
            rows=Plan.objects.filter(**scope).values('id','revision','code','name_en','name_ar','active')
        elif kind=='versions':
            rows=PlanVersion.objects.filter(plan__in=Plan.objects.filter(**scope,active=True)).values('id','plan__code','version','interval','price','currency')
        elif kind=='cycles':
            rows=Cycle.objects.filter(contract__in=Contract.objects.filter(**scope)).values('id','contract_id','start','end','due','invoice__number','invoice__total','invoice__paid','credit_used','schedule__warning','schedule__id')
        elif kind=='notices':
            rows=Notice.objects.filter(cycle__contract__in=Contract.objects.filter(**scope)).values('id','cycle_id','stage','delivered_at')
        else:
            rows=CancellationRequest.objects.filter(contract__in=Contract.objects.filter(**scope)).values('id','contract_id','contract__revision','status','reason')
        fields={'spaces':['name'],'plans':['code','name_en','name_ar'],'versions':['plan__code'],'parties':['name_en','name_ar'],'contracts':['party__name_en','party__name_ar','status'],'cycles':['invoice__number'],'requests':['status','reason']}.get(kind,[])
        result = page(data,rows.order_by('-id'),fields)
        if kind == 'cycles':
            for row in result['results']:
                has_schedule = row.pop('schedule__id') is not None
                row['gl_status'] = row.pop('schedule__warning') or ('posted' if has_schedule else 'awaiting_gl')
        if kind == 'contracts':
            from zoneinfo import ZoneInfo
            from django.utils import timezone
            for row in result['results']:
                row['can_bill'] = (row['next_date'] <= timezone.localdate(timezone=ZoneInfo(row.pop('zone')))
                                   and row['status'] in ('active','trial','past_due')
                                   and (not row['cancel_on'] or row['next_date'] < row['cancel_on']))
        return result
    def create():
        if kind=='plans':
            code=string(data,'code',40)
            if Plan.objects.filter(site_id=scope['site_id'],code=code).exists():
                raise Problem('duplicate_code',409)
            try:
                item=Plan.objects.create(**scope,code=code,name_en=string(data,'name_en',120),name_ar=string(data,'name_ar',120))
            except IntegrityError:
                raise Problem('duplicate_code',409)
            version(item,data)
        elif kind=='versions':
            item=one(Plan.objects.select_for_update().filter(**scope),data.get('plan_id'))
            revision(item,data)
            item.save()
            version(item,data)
        elif kind=='policy':
            from backbone.models import Site
            Site.objects.select_for_update().get(pk=scope['site_id'])
            item,_=DunningPolicy.objects.get_or_create(site_id=scope['site_id'],defaults={'revision':0})
            revision(item,data)
            days=[integer(data,k,1,3650,0) for k in ('first','second','final','suspend')]
            if days!=sorted(set(days)):
                raise Problem('invalid_notice_intervals')
            item.first,item.second,item.final,item.suspend=days
            item.save()
        elif kind=='contracts':
            plan=one(PlanVersion.objects.filter(plan__in=Plan.objects.filter(**scope,active=True)),data.get('version_id'))
            party=one(Party.objects.filter(**scope,active=True),data.get('party_id'))
            try:
                start=date.fromisoformat(str(data.get('start')))
                if not 1900<=start.year<=9900: raise ValueError
            except ValueError:
                raise Problem('invalid_date',field='start')
            paid=start+timedelta(days=plan.trial_days)
            zone=SiteSettings.objects.filter(site_id=scope['site_id']).values_list('timezone',flat=True).first() or 'UTC'
            item=Contract.objects.create(**scope,party=party,version=plan,start=start,next_date=paid,anchor=paid,
                zone=zone,status='trial' if plan.trial_days else 'active')
            engine.record(item,'created',request.user)
        else:
            raise Problem('method_not_allowed',405)
        audit(request.user,'subscriptions.'+kind,item.pk)
        return {'id':item.pk,'revision':item.revision}
    return once(request,'subscriptions.'+kind,data,create)

@endpoint('POST',module=MODULE)
def action(request,pk,action):
    permissions={'bill':'bill','change-plan':'manage','suspend':'override','resume':'override','cancel':'override','approve':'override','reject':'override'}
    if action not in permissions: raise Problem('not_found',404)
    data=body(request)
    scope=select_scope(request,data,'subscriptions.'+permissions[action])
    def apply():
        c=one(Contract.objects.select_for_update().filter(**scope),pk)
        revision(c,data)
        day=engine.today(c)
        if c.status=='cancelled':raise Problem('invalid_transition',409)
        if action=='bill':
            cycle=engine.bill(c.pk,c.next_date,request.user)
            return {'id':cycle.pk,'invoice_id':cycle.invoice_id,'revision':c.revision}
        if action=='change-plan':
            plan=one(PlanVersion.objects.filter(plan__in=Plan.objects.filter(**scope,active=True)),data.get('version_id'))
            engine.change_plan(c,plan,day)
        elif action=='suspend' and c.status in ('active','past_due'):
            c.status='suspended'
        elif action=='resume' and c.status=='suspended':
            c.status='active'
            if c.next_date<day:c.next_date=c.anchor=day
        elif action in ('cancel','approve','reject'):
            if action in ('approve','reject'):
                r=one(CancellationRequest.objects.select_for_update().filter(contract=c,status='pending'),data.get('request_id'))
                r.status='approved' if action=='approve' else 'rejected'
                r.save(update_fields=['status'])
            if action!='reject':
                # Only a settled invoice or a credit-covered cycle extends paid access.
                from django.db.models import Q
                paid=Cycle.objects.filter(contract=c,start__lte=day,end__gt=day).filter(Q(invoice=None)|Q(invoice__status='paid')).first()
                c.cancel_on=paid.end if paid else day
                if c.cancel_on<=day:c.status='cancelled'
        else:raise Problem('invalid_transition',409)
        c.save()
        engine.record(c,action,request.user)
        return {'id':c.pk,'revision':c.revision,'status':c.status}
    return once(request,f'subscriptions.{pk}.{action}',data,apply)

@endpoint('GET',module=MODULE)
def preview(request,pk):
    scope=select_scope(request,request.GET,'subscriptions.view')
    c=one(Contract.objects.filter(**scope),pk)
    lines,_,credit,plan,end=engine.quote(c,c.next_date)
    subtotal=sum((engine.money(engine.Decimal(row['unit_price'])) for row in lines),engine.Decimal(0))
    tax=sum((engine.money(engine.Decimal(row['unit_price'])*engine.Decimal(row['tax_rate'])/100) for row in lines),engine.Decimal(0))
    return dict(start=c.next_date,end=end,lines=lines,subtotal=subtotal,tax=tax,total=subtotal+tax-credit,credit_used=credit,currency=plan.currency)
