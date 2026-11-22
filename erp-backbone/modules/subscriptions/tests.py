from datetime import date,timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4
from django.contrib.auth import get_user_model
from django.test import TestCase,override_settings
from django.utils import timezone
from backbone.models import Site,ModuleState,Assignment,Role,OutboxEvent
from billing_core.models import Invoice
from accounting.models import Ledger
from parties.models import Party
from . import engine,tasks
from .models import Contract,Plan,PlanVersion,Cycle,Notice,Adjustment

class SubscriptionTests(TestCase):
    def setUp(self):
        for module in ('subscriptions','billing_core','parties','accounting'):
            ModuleState.objects.update_or_create(module_id=module,defaults={'enabled':True})
        ledger=Ledger.objects.get(pk=1)
        ledger.activated_at=timezone.now()
        ledger.save()
        self.site=Site.objects.create(name='DIB branch')
        self.party=Party.objects.create(site=self.site,name_en='Subscriber',name_ar='مشترك',email='subscriber@example.test')
        self.plan=Plan.objects.create(site=self.site,code='STD',name_en='Service',name_ar='خدمة')
        self.version=PlanVersion.objects.create(plan=self.plan,version=1,currency='AED',price=300,
            interval='monthly',tax=0,setup=20)
        self.day=date(2028,4,1)
        self.c=Contract.objects.create(site=self.site,party=self.party,version=self.version,zone='UTC',
            start=self.day,next_date=self.day,anchor=self.day,status='trial')
        self.clock=patch('subscriptions.engine.today',return_value=self.day).start()
        self.addCleanup(patch.stopall)

    def test_retry_finds_same_invoice_and_setup_once(self):
        cycle=engine.bill(self.c.pk,self.day)
        self.assertEqual(cycle.invoice.total,320)
        self.assertEqual(engine.bill(self.c.pk,self.day).invoice_id,cycle.invoice_id)
        self.clock.return_value=cycle.end
        self.assertEqual(engine.bill(self.c.pk,cycle.end).invoice.total,300)
        self.assertEqual(Invoice.objects.count(),2)

    def test_proration_at_ten_days_remaining(self):
        cycle=engine.bill(self.c.pk,self.day)
        upgrade=PlanVersion.objects.create(plan=self.plan,version=2,currency='AED',price=450,interval='monthly',tax=0,setup=0)
        self.c.refresh_from_db()
        engine.change_plan(self.c,upgrade,cycle.end-timedelta(days=10))
        self.c.save()
        self.assertEqual(Adjustment.objects.get().amount,50)
        self.clock.return_value=cycle.end
        self.assertEqual(engine.bill(self.c.pk,cycle.end).invoice.total,500)

    def test_credit_can_cover_an_entire_cycle(self):
        cycle=engine.bill(self.c.pk,self.day)
        adj=Adjustment.objects.create(contract=self.c,version=self.version,effective=self.day,amount=-400,remaining=-400,tax=0)
        self.clock.return_value=cycle.end
        next_cycle=engine.bill(self.c.pk,cycle.end)
        self.assertIsNone(next_cycle.invoice_id)
        adj.refresh_from_db()
        self.assertEqual(adj.remaining,-100)
        self.assertEqual(next_cycle.credit_used,300)

    def test_eom_and_leap(self):
        jan=date(2028,1,31)
        feb=engine.period_end(jan,jan,self.version)
        self.assertEqual(feb,date(2028,2,29))
        self.assertEqual(engine.period_end(feb,jan,self.version),date(2028,3,31))

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_failed_final_notice_prevents_suspension(self):
        engine.bill(self.c.pk,self.day)
        for stage,days in enumerate((1,7,14),1):
            self.clock.return_value=self.day+timedelta(days=days)
            tasks.process(self.c.pk)
            n=Notice.objects.get(stage=stage)
            if stage!=3:tasks.send_notice(n.pk)
        self.clock.return_value=self.day+timedelta(days=21)
        tasks.process(self.c.pk)
        self.c.refresh_from_db()
        self.assertEqual(self.c.status,'past_due')
        tasks.send_notice(n.pk)
        tasks.process(self.c.pk)
        self.c.refresh_from_db()
        self.assertEqual(self.c.status,'suspended')

    def test_paid_invoice_restores_past_due(self):
        cycle=engine.bill(self.c.pk,self.day)
        self.clock.return_value=self.day+timedelta(days=1)
        tasks.process(self.c.pk)
        Invoice.objects.filter(pk=cycle.invoice_id).update(status='paid',paid=320)
        tasks.process(self.c.pk)
        self.c.refresh_from_db()
        self.assertEqual(self.c.status,'active')

    def test_atomic_invoice_and_outbox(self):
        with patch('subscriptions.engine.issue',side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):engine.bill(self.c.pk,self.day)
        self.assertFalse(Cycle.objects.exists())
        self.assertFalse(Invoice.objects.exists())
        self.assertFalse(OutboxEvent.objects.filter(module='billing_core').exists())

    def test_api_scope_idempotency_and_revision(self):
        user=get_user_model().objects.create_user('billing-operator')
        role=Role.objects.create(name='Subscription billing',permissions=['subscriptions.view','subscriptions.bill'])
        Assignment.objects.create(user=user,site=self.site,role=role)
        self.client.force_login(user)
        path=f'/api/subscriptions/contracts/{self.c.pk}/bill'
        def post(site,revision,key):
            return self.client.post(path,{'site_id':site,'revision':revision},content_type='application/json',HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual(post(Site.objects.create(name='Elsewhere').pk,1,'other').status_code,403)
        self.assertEqual(post(self.site.pk,1,'same').status_code,201)
        self.assertEqual(post(self.site.pk,1,'same').status_code,200)
        self.assertEqual(post(self.site.pk,1,'stale').status_code,409)

    def test_broker_outage_and_replayed_worker(self):
        from kombu.exceptions import OperationalError
        with patch.object(tasks.app,'send_task',side_effect=OperationalError('broker offline')):
            tasks.dispatch()
        self.assertFalse(Cycle.objects.exists())
        self.assertTrue(OutboxEvent.objects.filter(module='subscriptions',event='cycle.queued').exists())
        Contract.objects.filter(pk=self.c.pk).update(next_check=timezone.now()-timedelta(seconds=1))
        with patch.object(tasks.app,'send_task') as broker:
            tasks.dispatch()
        args=broker.call_args.kwargs['args']
        tasks.process(*args)
        tasks.process(*args)
        self.assertEqual(Invoice.objects.count(),1)

    def test_frequency_change_on_renewal(self):
        cycle=engine.bill(self.c.pk,self.day)
        self.c.refresh_from_db()
        new=PlanVersion.objects.create(plan=self.plan,version=2,price=800,setup=0,tax=0,interval='quarterly',currency='AED')
        engine.change_plan(self.c,new,self.day+timedelta(days=1))
        self.c.save()
        self.assertEqual(self.c.version_id,self.version.pk)
        self.clock.return_value=cycle.end
        renewal=engine.bill(self.c.pk,cycle.end)
        self.assertEqual(renewal.end,date(2028,8,1))
        self.assertEqual(renewal.invoice.total,800)

    def test_portal_cancellation_pdf_and_party_boundary(self):
        from rest_framework.test import APIClient
        from publication.models import PublicationSpace
        from public_identity.models import ExternalIdentity,IdentityChallenge
        from public_identity.services import challenge_token
        from billing_core.models import Receipt
        for mod in ('publication','public_identity'):
            ModuleState.objects.update_or_create(module_id=mod,defaults={'enabled':True})
        space=PublicationSpace.objects.create(code='store',name='Store',internal_site=self.site)
        client=APIClient(enforce_csrf_checks=True)
        csrf=client.get('/api/public/v1/auth/csrf').json()['csrf_token']
        client.credentials(HTTP_X_CSRFTOKEN=csrf)
        data=dict(space='store',email='owner@example.test',password='Long-Test-Password!4367',display_name='Owner')
        with self.settings(DEBUG=True):
            r=client.post('/api/public/v1/auth/register',data,format='json')
            self.assertEqual(r.status_code,202,r.content)
            identity=ExternalIdentity.objects.get(normalized_email=data['email'])
            challenge=IdentityChallenge.objects.get(identity=identity,purpose='verify')
            client.post('/api/public/v1/auth/verify-email',dict(challenge_id=str(challenge.pk),token=challenge_token(challenge)),format='json')
            token=client.post('/api/public/v1/auth/login',data,format='json').json()['access']
        self.party.space=space
        self.party.save()
        identity.party=self.party
        identity.save(update_fields=['party'])
        self.c.space=space
        self.c.save()
        cycle=engine.bill(self.c.pk,self.day)
        self.c.refresh_from_db()
        receipt=Receipt.objects.create(site=self.site,space=space,number='RCT-test',invoice=cycle.invoice,amount=1)
        client.credentials(HTTP_AUTHORIZATION='Bearer '+token,HTTP_X_CSRFTOKEN=csrf)
        listing=client.get('/api/customer/v1/subscriptions'); self.assertEqual(listing.status_code,200,listing.content); self.assertEqual(len(listing.json()['results']),1)
        self.assertEqual(client.get('/api/subscriptions/contracts',{'site_id':self.site.pk}).status_code,401)
        url='/api/customer/v1/subscriptions/'+str(self.c.pk)+'/cancellation-requests'
        key=str(uuid4())
        payload={'reason':'End of term','revision':self.c.revision}
        self.assertEqual(client.post(url,payload,format='json',HTTP_IDEMPOTENCY_KEY=key).status_code,201)
        self.assertEqual(client.post(url,payload,format='json',HTTP_IDEMPOTENCY_KEY=key).status_code,200)
        for kind,pk in [('invoices',cycle.invoice_id),('receipts',receipt.pk)]:
            path=f'/api/customer/v1/business/{kind}/{pk}/download'
            for lang in ('ar','en'):
                r=client.get(path,{'lang':lang})
                self.assertEqual(r.status_code,200,r.content[:200])
                self.assertTrue(r.content.startswith(b'%PDF'))
        other=Party.objects.create(site=self.site,space=space,name_en='Other')
        ExternalIdentity.objects.filter(pk=identity.pk).update(party=other)
        self.assertEqual(client.get('/api/customer/v1/subscriptions').json()['results'],[])
        self.assertEqual(client.get(path).status_code,404)
        self.assertEqual(client.get(f'/api/customer/v1/subscriptions/{self.c.pk}').status_code,404)
        self.assertEqual(client.post(url,payload,format='json',HTTP_IDEMPOTENCY_KEY=key).status_code,404)

    def test_review_cancellation_at_end_of_paid_period(self):
        from .models import CancellationRequest
        cycle=engine.bill(self.c.pk,self.day)
        Invoice.objects.filter(pk=cycle.invoice_id).update(status='paid',paid=320)
        self.c.refresh_from_db()
        request=CancellationRequest.objects.create(contract=self.c,identity_id=uuid4(),key=uuid4(),reason='End service')
        staff=get_user_model().objects.create_user('reviewer')
        role=Role.objects.create(name='Cancellation reviewer',permissions=['subscriptions.override'])
        Assignment.objects.create(user=staff,site=self.site,role=role)
        self.client.force_login(staff)
        response=self.client.post(f'/api/subscriptions/contracts/{self.c.pk}/approve',
            dict(site_id=self.site.pk,revision=self.c.revision,request_id=request.pk),content_type='application/json',HTTP_IDEMPOTENCY_KEY='review')
        self.assertEqual(response.status_code,201,response.content)
        self.c.refresh_from_db()
        self.assertEqual(self.c.cancel_on,cycle.end)
        self.assertEqual(self.c.status,'active')
        self.clock.return_value=cycle.end
        tasks.process(self.c.pk)
        self.c.refresh_from_db()
        self.assertEqual(self.c.status,'cancelled')
        self.assertEqual(Invoice.objects.count(),1)

    def test_preview_is_read_only_and_core_events_wake_contract(self):
        from types import SimpleNamespace
        from .events import handle_event
        numbers=engine.quote(self.c,self.day)
        self.assertEqual(len(numbers[0]),2)
        self.assertFalse(Invoice.objects.exists())
        cycle=engine.bill(self.c.pk,self.day)
        from billing_core.models import Receipt
        receipt=Receipt.objects.create(site=self.site,invoice=cycle.invoice,number='RCT-test',amount=1)
        handle_event(SimpleNamespace(module='billing_core',event='receipt.recorded',object_id=receipt.pk))
        self.c.refresh_from_db()
        self.assertIsNotNone(self.c.next_check)

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.db import connections
from django.test import TransactionTestCase

class ConcurrentBillingTests(TransactionTestCase):
    def test_two_workers_one_invoice(self):
        ledger,_=Ledger.objects.get_or_create(pk=1)
        ledger.activated_at=timezone.now()
        ledger.save()
        site=Site.objects.create(name='Concurrency')
        party=Party.objects.create(site=site,name_en='Client',name_ar='عميل')
        plan=Plan.objects.create(site=site,code='P',name_en='Monthly',name_ar='شهري')
        version=PlanVersion.objects.create(plan=plan,version=1,currency=ledger.currency,price=10,setup=1,tax=5,interval='monthly')
        day=timezone.now().date()
        contract=Contract.objects.create(party=party,site=site,version=version,zone='UTC',start=day,next_date=day,anchor=day,status='active')
        barrier=Barrier(2,timeout=10)
        def worker():
            try:
                barrier.wait()
                return engine.bill(contract.pk,day).invoice_id
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(worker) for _ in range(2)]
            ids=[f.result(timeout=20) for f in futures]
        self.assertEqual(ids[0],ids[1])
        self.assertEqual(Invoice.objects.get(pk=ids[0]).total,Decimal('11.55'))
        self.assertEqual(Cycle.objects.filter(contract=contract).count(),1)

    def test_prior_credit_respects_original_tax_across_rate_change(self):
        ledger,_=Ledger.objects.get_or_create(pk=1)
        ledger.activated_at=timezone.now()
        ledger.save()
        site=Site.objects.create(name='Different VAT')
        party=Party.objects.create(site=site,name_en='Client')
        plan=Plan.objects.create(site=site,code='VAT',name_en='Monthly',name_ar='شهري')
        version=PlanVersion.objects.create(plan=plan,version=1,currency=ledger.currency,price=100,setup=0,tax=10,interval='monthly')
        day=timezone.now().date()
        contract=Contract.objects.create(party=party,site=site,version=version,zone='UTC',start=day,next_date=day,anchor=day,status='active')
        Adjustment.objects.create(contract=contract,version=version,effective=day,amount=-20,remaining=-21,tax=5)
        doc=engine.bill(contract.pk,day).invoice
        self.assertEqual((doc.subtotal,doc.tax,doc.applied_credit,doc.total),(100,10,21,89))
