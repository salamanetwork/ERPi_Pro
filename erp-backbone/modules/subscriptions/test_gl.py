from datetime import date,timedelta
from decimal import Decimal as D
from io import StringIO
from unittest.mock import patch
from django.core.management import call_command
from django.db.models import Sum
from django.test import TestCase
from django.utils import timezone
from accounting.models import Account, Ledger, Period, Line
from backbone.models import Site, OutboxEvent
from backbone.validation import Problem
from billing_core.models import Invoice,Receipt
from parties.models import Party
from . import engine, gl
from .models import (Adjustment,Contract,Plan,PlanVersion,SubscriptionGLMapping,
                     SubscriptionGLPosting as Posting, SubscriptionRevenueSchedule as Schedule)

class RevenueTests(TestCase):
    def setUp(self):
        self.site=Site.objects.create(name='Test Site A')
        self.ledger=Ledger.objects.get(pk=1)
        self.ledger.activated_at=timezone.now()
        self.ledger.save()
        self.start=date(2028,4,1)
        self.period=Period.objects.create(ledger=self.ledger,start=self.start,end=date(2028,8,31))
        party=Party.objects.create(site=self.site,name_en='Subscriber')
        plan=Plan.objects.create(site=self.site,code='REV',name_en='Standard')
        self.v=PlanVersion.objects.create(plan=plan,version=1,currency='AED',price=D('300'),setup=D('50'),tax=D('5'),interval='monthly')
        self.contract=Contract.objects.create(site=self.site,party=party,version=self.v,zone='UTC',
            start=self.start,next_date=self.start,anchor=self.start,status='active')
        self.mapping=SubscriptionGLMapping.objects.create(site=self.site,**{
            field:Account.objects.create(ledger=self.ledger,code='SUB'+field,name=field,kind=kind,detail=True)
            for field,kind in gl.KINDS.items()})
        clock=patch('subscriptions.engine.today',return_value=self.start)
        self.billclock=clock.start()
        self.addCleanup(clock.stop)
        clock=patch('subscriptions.gl.today',return_value=self.start)
        self.clock=clock.start()
        self.addCleanup(clock.stop)
        self.c=engine.bill(self.contract.pk,self.start)

    def sync(self,days=0):
        self.clock.return_value=self.start+timedelta(days=days)
        return gl.sync(self.c.pk)

    def balance(self,field):
        return Line.objects.filter(account=getattr(self.mapping,field)).aggregate(s=Sum('amount'))['s'] or D(0)

    def receipt(self,value):
        r=Receipt.objects.create(site=self.site,invoice=self.c.invoice,number='RCT',amount=value)
        Receipt.objects.filter(pk=r.pk).update(created_at=timezone.make_aware(timezone.datetime(2028,4,1)))
        return r

    def test_invoice_matrix(self):
        self.sync()
        self.assertEqual([self.balance(k) for k in ('receivable','deferred','setup','vat')],[D('367.50'),-300,-50,D('-17.50')])
    def test_invoice_replay(self):
        self.sync();self.sync()
        self.assertEqual(Posting.objects.count(),1)
    def test_before_elapsed_service(self):
        self.assertEqual(self.sync().recognized,0)
    def test_elapsed_days(self):
        self.assertEqual(self.sync(10).recognized,100)
    def test_cutoff_replay(self):
        self.sync(3);self.sync(3)
        self.assertEqual(Posting.objects.filter(key__startswith='gl:sub_rev:').count(),3)
    def test_final_cent(self):
        # May is a 31-day cycle.
        self.billclock.return_value=self.c.end
        self.c=engine.bill(self.contract.pk,self.c.end)
        self.clock.return_value=self.c.end
        self.assertEqual(gl.sync(self.c.pk).recognized,300)
        self.assertEqual(self.balance('deferred'),0)
    def test_closed_period(self):
        self.period.closed_at=timezone.now();self.period.save()
        self.assertEqual(self.sync().warning,'finance_period_closed')
        self.assertFalse(Posting.objects.exists())
    def test_missing_mapping(self):
        self.mapping.delete()
        self.assertEqual(self.sync().warning,'subscription_gl_mapping_missing')
        self.assertEqual(self.c.invoice.total,D('367.50'))
    def test_invalid_type(self):
        self.mapping.bank=self.mapping.vat;self.mapping.save()
        self.assertEqual(self.sync().warning,'invalid_gl_account')
    def test_duplicate_accounts(self):
        self.mapping.bank=self.mapping.receivable;self.mapping.save()
        self.assertEqual(self.sync().warning,'distinct_gl_accounts_required')
    def test_currency_mismatch(self):
        self.ledger.currency='USD';self.ledger.save()
        self.assertEqual(self.sync().warning,'inactive_billing_currency')
    def test_partial_receipt(self):
        self.receipt('100');self.sync()
        self.assertEqual(self.balance('receivable'),D('267.50'))
    def test_multiple_receipts(self):
        self.receipt('100');r=self.receipt('267.50');self.sync();self.sync()
        self.assertEqual(self.balance('receivable'),0)
        self.assertTrue(Posting.objects.filter(key=f'gl:sub_rcpt:{r.pk}').exists())
    def test_credit_tax(self):
        Adjustment.objects.create(contract=self.contract,version=self.v,effective=self.start,amount=-100,remaining=-105,tax=5)
        self.sync()
        self.assertEqual(self.balance('vat'),D('-12.50'))
        self.assertEqual(self.balance('receivable'),D('262.50'))
    def test_downgrade_pacing(self):
        Adjustment.objects.create(contract=self.contract,version=self.v,effective=self.start+timedelta(days=20),amount=-50,remaining=D('-52.50'),tax=5)
        self.assertEqual(self.sync(30).recognized,250)
        self.assertEqual(self.balance('deferred'),0)
    def test_full_credit_cycle(self):
        Adjustment.objects.create(contract=self.contract,version=self.v,effective=self.start,amount=-300,remaining=-315,tax=5)
        self.sync(30)
        self.billclock.return_value=self.c.end
        cycle=engine.bill(self.contract.pk,self.c.end)
        self.assertIsNone(cycle.invoice_id)
        gl.sync(cycle.pk)
        self.assertEqual(self.balance('receivable'),D('367.50'))
    def test_void_reverses_setup_and_vat(self):
        self.sync()
        Invoice.objects.filter(pk=self.c.invoice_id).update(status='void')
        e=OutboxEvent.objects.create(module='billing_core',event='invoice.void',object_id=self.c.invoice_id,key='test:void')
        OutboxEvent.objects.filter(pk=e.pk).update(created_at=timezone.make_aware(timezone.datetime(2028,4,1)))
        self.sync()
        self.assertEqual(self.balance('setup'),0)
        self.assertEqual(self.balance('receivable'),0)
        self.assertIsNotNone(Posting.objects.get(key__startswith='gl:sub_void').entry.reversal_of_id)
    def test_recognized_void_guard(self):
        self.sync(1)
        with self.assertRaisesMessage(Problem,'already_recognized'):gl.protect_void(self.c.invoice)
    def test_dry_run(self):
        call_command('backfill_subscription_gl',site=self.site.name,dry_run=True,stdout=StringIO())
        self.assertFalse(Posting.objects.exists())
        self.assertFalse(Schedule.objects.exists())
    def test_backfill_replay(self):
        for _ in range(2):call_command('backfill_subscription_gl',site=self.site.name,stdout=StringIO())
        self.assertEqual(Posting.objects.count(),1)
    def test_site_isolation(self):
        other=Site.objects.create(name='Second')
        call_command('backfill_subscription_gl',site=other.name,stdout=StringIO())
        self.assertFalse(Posting.objects.exists())
    def test_trial_balance(self):
        self.receipt('30');self.sync(3)
        for p in Posting.objects.all():
            self.assertEqual(p.entry.lines.aggregate(total=Sum('amount'))['total'],0)
        self.assertEqual(self.balance('bank'),30)
        self.assertEqual(self.balance('deferred'),-270)

    def test_period_reopened_posts_once(self):
        self.period.closed_at=timezone.now();self.period.save()
        self.sync(1)
        self.period.closed_at=None;self.period.save()
        self.assertEqual(self.sync(1).warning,'')
        self.assertEqual(self.balance('revenue'),-10)

    def test_void_does_not_block_other_documents(self):
        self.sync(1)
        self.billclock.return_value=self.c.end
        other=engine.bill(self.contract.pk,self.c.end)
        gl.protect_void(other.invoice)

    def test_outbox_recovery_dispatch(self):
        from backbone.models import ModuleState
        from backbone.registry import MANIFESTS
        from backbone.module_events import publish, dispatch
        for name in MANIFESTS:
            ModuleState.objects.update_or_create(module_id=name,defaults={'enabled':True})
        publish('subscriptions','recognition.due',self.c.pk,'test:recognition:recovery')
        self.assertFalse(Posting.objects.exists())
        dispatch()
        self.assertTrue(Posting.objects.filter(key=f'gl:sub_inv:{self.c.invoice_id}').exists())
        dispatch()
        self.assertEqual(Posting.objects.count(),1)
