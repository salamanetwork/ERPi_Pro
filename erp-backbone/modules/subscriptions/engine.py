"""Advance billing. All writers lock the contract before its child records."""
import calendar
from datetime import date, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
from django.db import transaction
from django.utils import timezone
from backbone.models import AuditEvent
from backbone.module_events import publish
from backbone.validation import Problem
from billing_core.services import draft, issue, money
from .models import Adjustment, Allocation, Contract, Cycle

def today(contract):
    return timezone.localdate(timezone=ZoneInfo(contract.zone))

def period_end(start, anchor, version):
    if version.interval == 'custom':
        return start + timedelta(days=version.days)
    months = {'monthly':1, 'quarterly':3, 'annual':12}[version.interval]
    year, month = divmod(start.year * 12 + start.month - 1 + months, 12)
    last = calendar.monthrange(year, month + 1)[1]
    anchored_end = anchor.day == calendar.monthrange(anchor.year, anchor.month)[1]
    return date(year, month + 1, last if anchored_end else min(anchor.day,last))

def record(contract, action, actor=None):
    AuditEvent.objects.create(actor_user_id=getattr(actor,'pk',None),
                              actor=actor.get_username() if actor else 'subscriptions.worker',
                              action='subscriptions.'+action, target=str(contract.pk))
    publish('subscriptions','contract.changed',contract.pk,f'subscription:{contract.pk}:{contract.revision}:{action}')

def change_plan(contract, version, day):
    old = contract.version
    if old.currency != version.currency:
        raise Problem('currency_mismatch',409)
    cycle = Cycle.objects.filter(contract=contract,start__lte=day,end__gt=day).first()
    if cycle:
        if (old.interval,old.days,old.tax)!=(version.interval,version.days,version.tax):
            contract.renewal_version=version
            return
        delta = money((version.price-old.price)*(cycle.end-day).days/(cycle.end-cycle.start).days)
        if delta:
            adj = Adjustment.objects.create(contract=contract,version=version,effective=day,amount=delta,remaining=(delta+money(delta*old.tax/100) if delta<0 else delta),tax=old.tax)
            if delta < 0:
                publish("subscriptions","credit.created",adj.pk,f"subscription:credit:{adj.pk}")
    contract.renewal_version=None
    contract.version=version

@transaction.atomic
def bill(pk, expected, actor=None):
    from backbone.models import Site
    site_id=Contract.objects.values_list('site_id',flat=True).get(pk=pk)
    if not Site.objects.select_for_update().filter(pk=site_id,active=True).exists():
        raise Problem('inactive_site',409)
    c=Contract.objects.select_for_update().get(pk=pk)
    if not c.party.active:
        raise Problem('inactive_party',409)
    from accounting.models import Ledger
    if not Ledger.objects.filter(pk=1,activated_at__isnull=False,currency=c.version.currency).exists():
        raise Problem('inactive_billing_currency',409)
    existing=Cycle.objects.filter(contract=c,start=expected).first()
    if existing:
        return existing
    if c.next_date!=expected or today(c)<expected or c.status not in ('active','trial','past_due') or (c.cancel_on and expected>=c.cancel_on):
        raise Problem('cycle_not_due',409)
    lines,allocations,credit,plan,end=quote(c,expected)
    cycle=Cycle.objects.create(contract=c,version=plan,start=expected,end=end,due=expected,credit_used=credit)
    for adjustment,applied in allocations:
        adjustment.remaining-=applied
        adjustment.save(update_fields=['remaining'])
        Allocation.objects.create(cycle=cycle,adjustment=adjustment,amount=applied)
    if c.renewal_version_id:
        c.version=plan
        c.anchor=expected
        c.renewal_version=None
    if gross_total(lines)>credit:
        doc,event=draft(dict(party_id=c.party_id,currency=plan.currency,lines=lines),dict(site_id=c.site_id,space_id=c.space_id),applied_credit=credit)
        publish('billing_core',event,doc.pk,f'billing_core:subscription:{cycle.pk}:created')
        doc.revision+=1
        issue(doc)
        cycle.invoice=doc
    cycle.save()
    c.next_date=end
    if c.status=='trial':
        c.status='active'
    c.revision+=1
    c.save()
    record(c,'bill',actor)
    publish('subscriptions','cycle.billed',cycle.pk,f'subscription:cycle:{cycle.pk}:billed')
    return cycle

def quote(c,start):
    plan=c.renewal_version or c.version
    end=period_end(start,start if c.renewal_version_id else c.anchor,plan)
    charges=[(plan.plan.name_en,plan.price,plan.tax)]
    if not Cycle.objects.filter(contract=c).exists() and plan.setup:
        charges.append(('Setup / تأسيس',plan.setup,plan.tax))
    adjustments=list(Adjustment.objects.filter(contract=c).exclude(remaining=0).order_by('pk'))
    allocations=[]
    credits=[]
    for adjustment in adjustments:
        if adjustment.remaining>0:
            charges.append(('Proration / تسوية',adjustment.remaining,adjustment.tax))
            allocations.append((adjustment,adjustment.remaining))
        else:
            credits.append([adjustment,-adjustment.remaining])
    lines=[dict(description=label,quantity='1',unit_price=str(price),tax_rate=str(tax))
           for label,price,tax in charges if price]
    gross=gross_total(lines)
    available=gross
    for adj,balance in credits:
        used=min(balance,available)
        if used:
            allocations.append((adj,-used))
            available-=used
    return lines,allocations,gross-available,plan,end

def gross_total(lines):
    total=Decimal(0)
    for row in lines:
        net=money(Decimal(row['unit_price']))
        total+=net+money(net*Decimal(row['tax_rate'])/100)
    return total
