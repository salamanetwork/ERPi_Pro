from uuid import uuid4
from django.test import TestCase
from django.contrib.auth import get_user_model
from backbone.mfa import VERIFIED,stamp
from backbone.models import Assignment,Authenticator,ModuleState,Site,Role,OutboxEvent,AuditEvent
from publication.models import PublicationSpace
from billing_core.models import Invoice

class SharedServicesTests(TestCase):
    def setUp(self):
        for key in ['publication','public_identity','media_service','parties','scheduling','billing_core']:
            ModuleState.objects.update_or_create(module_id=key,defaults={'enabled':True})
        self.site=Site.objects.create(name='One')
        self.other=Site.objects.create(name='Other')
        self.scope={'site_id':self.site.pk}
        user=get_user_model().objects.create_user('operator')
        device=Authenticator.objects.create(user=user,encrypted_key='session-device')
        self.client.force_login(user)
        session=self.client.session
        session[VERIFIED]=stamp(device)
        session.save()
        permissions=['parties.view','parties.manage','scheduling.view','scheduling.manage','billing_core.view','billing_core.manage','billing_core.issue','billing_core.pay']
        self.role=Role.objects.create(name='Manager',permissions=permissions)
        Assignment.objects.create(user=user,role=self.role,site=self.site)
        self.user=user
    def post(self,path,data,expect=201,key=None):
        r=self.client.post('/api/'+path,{**self.scope,**data},content_type='application/json',HTTP_IDEMPOTENCY_KEY=key or str(uuid4()))
        self.assertEqual(r.status_code,expect,r.content)
        return r.json()
    def party(self):
        return self.post('parties/records',{'name_ar':'الشركة','name_en':'Company','party_type':'customer'})['id']
    def test_scope(self):
        space=PublicationSpace.objects.create(name='Branch',internal_site=self.site,code='branch')
        self.post('parties/records',dict(name_ar='شركة',name_en='Only here',party_type='both',space_id=space.pk))
        url='/api/parties/records'
        self.assertEqual(self.client.get(url,self.scope).json()['count'],0)
        self.assertEqual(self.client.get(url,{**self.scope,'space_id':space.pk}).json()['count'],1)
        self.assertEqual(self.client.get(url,{'site_id':self.other.pk}).status_code,403)
        self.role.permissions=[];self.role.save()
        self.assertEqual(self.client.get(url,self.scope).status_code,403)
    def test_booking(self):
        party=self.party()
        resource=self.post('scheduling/resources',dict(name='Desk',weekdays=list(range(7)),opens='08:00',closes='18:00',timezone='Asia/Dubai',slot_minutes=30))
        data=dict(resource_id=resource['id'],party_id=party,start='2026-11-18T09:00:00+04:00')
        appt=self.post('scheduling/appointments',data)
        self.post('scheduling/appointments',data,409)
        self.post('scheduling/appointments',{**data,'start':'2026-11-18T07:30:00+04:00'},400)
        self.post('scheduling/appointments',{**appt,'status':'confirmed'})
        self.post('scheduling/appointments',{**appt,'status':'cancelled'},409)
        self.post('scheduling/appointments',{**appt,'revision':2,'status':'cancelled'})
        self.assertEqual(OutboxEvent.objects.filter(module='scheduling',event__startswith='appointment.').count(),3)
    def test_billing(self):
        party=self.party()
        draft=self.post('billing_core/invoices',dict(party_id=party,currency='AED',lines=[dict(description='Service',quantity='2',unit_price='50',tax_rate='5')]))
        doc=Invoice.objects.get(pk=draft['id'])
        self.assertEqual(str(doc.total),'105.00')
        url=f'billing_core/invoices/{doc.pk}/'
        self.post(url+'issue',{'revision':1})
        p=self.post(url+'pay',{'revision':2,'amount':'25'})
        self.assertEqual(p['status'],'partially_paid')
        self.post(url+'pay',{'revision':3,'amount':'81'},400)
        key=str(uuid4())
        self.post(url+'pay',{'revision':3,'amount':'80'},key=key)
        self.post(url+'pay',{'revision':3,'amount':'80'},200,key)
        self.assertEqual(OutboxEvent.objects.filter(event='invoice.paid',module='billing_core').count(),1)
        printurl='/api/billing_core/receipts/'+str(p['receipt'])+'/print'
        self.assertContains(self.client.get(printurl,self.scope),'25.00')
        self.assertEqual(self.client.get(printurl,{'site_id':self.other.pk}).status_code,403)
    def test_audit_paging(self):
        role=Role.objects.create(name='Auditor',permissions=['platform.audit'])
        Assignment.objects.create(user=self.user,role=role)
        for i in range(27): AuditEvent.objects.create(actor='operator',action='test.note',target=str(i))
        r=self.client.get('/api/audit',dict(page=3,page_size=10,q='test.note')).json()
        self.assertEqual((r['count'],len(r['events']),r['page']),(27,7,3))

    def test_legacy_directories_accept_shared_toolbar(self):
        self.user.is_superuser=True
        self.user.save()
        from backbone.registry import MANIFESTS
        for module in MANIFESTS:
            if module!='platform':ModuleState.objects.update_or_create(module_id=module,defaults={'enabled':True})
        for url in ['sites','assets','work-orders','purchase-requests','inventory/receipts','inventory/balances','inventory/movements',
                    'sales/documents','ap/invoices','hr/leaves','hr/payroll','hr/payslips','inbox','notifications',
                    'admin/users','access/assignments','platform/v1/spaces','crm/leads','projects','manufacturing/boms']:
            with self.subTest(url=url):
                r=self.client.get('/api/'+url,dict(q='testing',page=1,page_size=10))
                self.assertEqual(r.status_code,200,r.content)
                self.assertIn('count',r.json())

    def test_bridge_and_detail_scope(self):
        from sales.models import Customer
        from parties.models import Party
        party=Party.objects.create(site=self.site,party_type='both',identity_number='TX001')
        self.assertEqual(Customer.objects.create(code='C1',tax_id='TX001').party_id,party.pk)
        Party.objects.create(site=self.other,party_type='both',identity_number='TX001')
        self.assertIsNone(Customer.objects.create(code='C2',tax_id='TX001').party_id)
        url=f'/api/parties/records/{party.pk}'
        self.assertNotIn('customers',self.client.get(url,self.scope).json()['sections'])
        self.assertEqual(self.client.get(url,{'site_id':self.other.pk}).status_code,403)
