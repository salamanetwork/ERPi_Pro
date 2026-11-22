"""Subscription subledger. Invoice -> cycle -> mapping is the shared lock order."""
from datetime import timedelta
from decimal import Decimal
from functools import partial
from zoneinfo import ZoneInfo
from django.db import transaction
from django.utils import timezone
from accounting.models import Ledger
from accounting.services import period_for, posted_entry
from backbone.models import OutboxEvent
from backbone.validation import Problem
from billing_core.models import Invoice, Receipt
from billing_core.services import money
from .engine import today
from .models import (Adjustment, Allocation, Cycle, SubscriptionGLMapping,
                     SubscriptionGLPosting, SubscriptionRevenueSchedule)

KINDS = dict(receivable='asset', deferred='liability', revenue='revenue',
             setup='revenue', vat='liability', bank='asset')
DAY = timedelta(days=1)
ZERO = Decimal('0.00')


def validate(mapping, currency):
    ledger = Ledger.objects.filter(pk=1, activated_at__isnull=False, currency=currency).first()
    if not ledger:
        raise Problem('inactive_billing_currency', 409)
    for field, kind in KINDS.items():
        account = getattr(mapping, field)
        if (account.kind != kind or not account.active or not account.detail
                or account.ledger_id != ledger.pk):
            raise Problem('invalid_gl_account', field=field)
    if len({getattr(mapping, field).pk for field in KINDS}) != len(KINDS):
        raise Problem('distinct_gl_accounts_required', 409)
    return ledger


@transaction.atomic
def post(cycle, ledger, key, day, values, *, preview=None, created=None, **kwargs):
    previous = SubscriptionGLPosting.objects.filter(key=key).first()
    if previous:
        return previous.entry
    lines = [(account, money(amount), cycle.contract.site_id, None)
             for account, amount in values if money(amount)]
    if not lines:
        return None
    period = period_for(ledger, day)
    if preview is not None:
        if sum(amount for _, amount, _, _ in lines):
            raise Problem('finance_unbalanced',409)
        preview.add(key)
        return None
    entry = posted_entry(ledger, period, day, 'subscriptions.worker',
                         key, 'subscription', lines, **kwargs)
    SubscriptionGLPosting.objects.create(cycle=cycle, key=key, entry=entry)
    if created is not None:
        created.add(key)
    return entry


def protect_void(invoice):
    # Caller owns the invoice row lock, also acquired before every recognition.
    if SubscriptionRevenueSchedule.objects.filter(cycle__invoice=invoice, recognized__gt=0).exists():
        raise Problem('subscription_revenue_already_recognized', 409)
    cycle = Cycle.objects.filter(invoice=invoice).first()
    if cycle and (cycle.credit_used or credits_for(cycle).exists()):
        raise Problem('subscription_credit_adjustment_required', 409)


def credits_for(cycle):
    return Adjustment.objects.filter(contract=cycle.contract, effective__gte=cycle.start,
                                     effective__lt=cycle.end, amount__lt=0).order_by('effective','pk')


def synchronize(cycle, schedule, doc, mapping, cutoff, preview=None, created=None):
    ledger = validate(mapping, cycle.version.currency)
    journal = partial(post, cycle, ledger, preview=preview, created=created)
    version = cycle.version
    setup = version.setup if not Cycle.objects.filter(contract=cycle.contract,start__lt=cycle.start).exists() else ZERO
    extras = Allocation.objects.filter(cycle=cycle, amount__gt=0)
    plan = version.price + sum((x.amount for x in extras), ZERO)
    tax = (doc.tax if doc else money(version.price*version.tax/100) + money(setup*version.tax/100)
           + sum((money(a.amount*a.adjustment.tax/100) for a in extras), ZERO))
    gross = plan + setup + tax
    if doc and (doc.currency != ledger.currency or gross != doc.total + doc.applied_credit):
        raise Problem('subscription_invoice_totals_mismatch', 409)
    key = f'gl:sub_inv:{doc.pk}' if doc else f'gl:sub_cycle:{cycle.pk}'
    issue_lines = [
        (mapping.receivable, gross-cycle.credit_used), (mapping.receivable, cycle.credit_used),
        (mapping.deferred,-plan), (mapping.setup,-setup), (mapping.vat,-tax)]
    issue = journal(key, cycle.start, issue_lines)
    if doc and doc.status == 'void':
        protect_void(doc)
        event = OutboxEvent.objects.get(module='billing_core', event='invoice.void',object_id=doc.pk)
        day = timezone.localdate(event.created_at, ZoneInfo(cycle.contract.zone))
        journal(f'gl:sub_void:{doc.pk}', day,
             [(account,-amount) for account,amount in issue_lines], reversal_of=issue)
        schedule.complete = True
        return
    if doc:
        for receipt in Receipt.objects.filter(invoice=doc).order_by('pk'):
            day = timezone.localdate(receipt.created_at, ZoneInfo(cycle.contract.zone))
            journal(f'gl:sub_rcpt:{receipt.pk}', day,
                 [(mapping.bank,receipt.amount),(mapping.receivable,-receipt.amount)])
    credits = list(credits_for(cycle).filter(effective__lte=cutoff))
    for credit in credits:
        net = -credit.amount
        vat = money(net*credit.tax/100)
        journal(f'gl:sub_credit:{credit.pk}', credit.effective,
             [(mapping.deferred, net),(mapping.vat, vat),(mapping.receivable,-net-vat)])
    end = min(cutoff, today(cycle.contract)-DAY, cycle.end-DAY)
    day = schedule.last_cutoff + DAY if schedule.last_cutoff else cycle.start
    while day <= end:
        # Cumulative targets absorb rounding on the final service day.
        target = plan*(day+DAY-cycle.start).days/(cycle.end-cycle.start).days
        for adj in credits:
            if adj.effective <= day:
                target += adj.amount*(day+DAY-adj.effective).days/(cycle.end-adj.effective).days
        target = money(target)
        delta = target - schedule.recognized
        journal(f'gl:sub_rev:{cycle.pk}:{day}', day,
             [(mapping.deferred,delta),(mapping.revenue,-delta)])
        schedule.last_cutoff, schedule.recognized = day, target
        day += DAY
    schedule.complete = schedule.last_cutoff == cycle.end-DAY


@transaction.atomic
def sync(pk, cutoff=None, *, preview=None, created=None):
    invoice_id = Cycle.objects.values_list('invoice_id',flat=True).get(pk=pk)
    doc = Invoice.objects.select_for_update().get(pk=invoice_id) if invoice_id else None
    cycle = Cycle.objects.select_for_update().get(pk=pk)
    schedule = SubscriptionRevenueSchedule.objects.filter(cycle=cycle).first() or SubscriptionRevenueSchedule(cycle=cycle,recognized=ZERO)
    mapping = SubscriptionGLMapping.objects.select_for_update().filter(site_id=cycle.contract.site_id).first()
    try:
        if not mapping:
            raise Problem('subscription_gl_mapping_missing',409)
        synchronize(cycle, schedule, doc, mapping, min(cutoff or today(cycle.contract), today(cycle.contract)), preview, created)
    except Problem as exc:
        schedule.complete = False
        schedule.warning = exc.code
    else:
        schedule.warning = ''
    if preview is None:
        schedule.save()
    return schedule
