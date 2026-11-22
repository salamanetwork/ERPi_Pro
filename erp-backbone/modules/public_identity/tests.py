from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from tempfile import TemporaryDirectory
from uuid import uuid4
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connections
from django.test import TransactionTestCase,Client,override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from PIL import Image
from backbone.models import ModuleState,Site,Role,Assignment
from publication.models import PublicationSpace
from media_service.models import MediaAsset
from .models import ExternalIdentity,ExternalSession,IdentityChallenge
from .services import challenge_token

@override_settings(DEBUG=True)
class FoundationTests(TransactionTestCase):
    def setUp(self):
        for key in ["publication","public_identity","media_service"]: ModuleState.objects.update_or_create(module_id=key,defaults={"enabled":True})
        self.site=Site.objects.create(name="Foundation East")
        self.space=PublicationSpace.objects.create(code="main",name="Main",internal_site=self.site)
        self.secret="Long-Test-Password!4367"
        self.client=APIClient(enforce_csrf_checks=True)
        self.csrf(self.client)
        media=self.enterContext(TemporaryDirectory())
        self.enterContext(self.settings(MEDIA_ROOT=media))
    def csrf(self,client):
        response=client.get("/api/public/v1/auth/csrf")
        self.assertEqual(response.status_code,200,response.content)
        client.credentials(HTTP_X_CSRFTOKEN=response.json()["csrf_token"])
    def post(self,action,data,status=200,client=None):
        response=(client or self.client).post("/api/public/v1/auth/"+action,data,format="json")
        self.assertEqual(response.status_code,status,response.content)
        return response
    def account(self,address="ahmed@example.test"):
        self.post("register",{"space":"main","email":address,"password":self.secret,"display_name":"Ahmed"},202)
        row=ExternalIdentity.objects.get(normalized_email=address)
        link=IdentityChallenge.objects.get(identity=row,purpose="verify",consumed_at=None)
        self.post("verify-email",{"challenge_id":str(link.pk),"token":challenge_token(link)})
        return row
    def login(self,address="ahmed@example.test",client=None):
        return self.post("login",{"space":"main","email":address,"password":self.secret},client=client).json()["access"]
    def bearer(self,access,client=None):
        target=client or self.client
        target.credentials(HTTP_AUTHORIZATION="Bearer "+access,HTTP_X_CSRFTOKEN=target.cookies["csrftoken"].value)
    def image(self,name="photo.png"):
        out=BytesIO();Image.new("RGB",(3,3),"blue").save(out,format="PNG")
        return SimpleUploadedFile(name,out.getvalue(),content_type="image/png")
    def test_customer_never_gets_internal_access(self):
        before=get_user_model().objects.count()
        identity=self.account();access=self.login();self.bearer(access)
        self.assertEqual(get_user_model().objects.count(),before)
        self.assertEqual(self.client.get("/api/customer/v1/me").json()["id"],str(identity.pk))
        for path in ["/api/workspace","/api/platform/v1/spaces","/api/purchase-requests","/api/platform/v1/identities","/api/attendance/me/status"]:
            self.assertEqual(self.client.get(path).status_code,401,path)
        claims=__import__("public_identity.security",fromlist=["decode"]).decode(access,"access")
        self.assertNotIn("user_id",claims);self.assertEqual(claims["aud"],"erpi.customer")
    def test_pending_verification_expiry_and_csrf(self):
        raw=APIClient(enforce_csrf_checks=True)
        self.assertEqual(raw.post("/api/public/v1/auth/register",{},format="json").status_code,403)
        self.post("register",{"space":"main","email":"AHMED@example.test","password":self.secret,"display_name":"Ahmed"},202)
        self.post("login",{"space":"main","email":"ahmed@example.test","password":self.secret},401)
        row=IdentityChallenge.objects.get()
        IdentityChallenge.objects.filter(pk=row.pk).update(expires_at=timezone.now())
        self.post("verify-email",{"challenge_id":str(row.pk),"token":challenge_token(row)},400)
        self.post("resend-verification",{"space":"main","email":"ahmed@example.test"},202)
        row=IdentityChallenge.objects.get(consumed_at=None)
        data={"challenge_id":str(row.pk),"token":challenge_token(row)}
        self.post("verify-email",data)
        self.post("verify-email",data,400)
    def test_refresh_rotates_and_reuse_revokes(self):
        self.account();self.login();old=self.client.cookies["erpi_customer_refresh"].value
        response=self.post("refresh",{})
        new=self.client.cookies["erpi_customer_refresh"].value
        self.assertNotEqual(new,old)
        self.assertTrue(self.client.cookies["erpi_customer_refresh"]["httponly"])
        self.assertNotEqual(ExternalSession.objects.get().refresh_jti_hash,new)
        self.client.cookies["erpi_customer_refresh"]=old
        self.post("refresh",{},401)
        self.bearer(response.json()["access"])
        self.assertEqual(self.client.get("/api/customer/v1/me").status_code,401)
    def test_two_refreshes_only_one_succeeds(self):
        self.account();self.login();old=self.client.cookies["erpi_customer_refresh"].value
        token=self.client.cookies["csrftoken"].value
        def refresh(_):
            try:
                c=APIClient(enforce_csrf_checks=True);c.cookies["erpi_customer_refresh"]=old;c.cookies["csrftoken"]=token
                return c.post("/api/public/v1/auth/refresh",{},format="json",HTTP_X_CSRFTOKEN=token).status_code
            finally: connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool: results=sorted(pool.map(refresh,range(2)))
        self.assertEqual(results,[200,401])
    def test_private_media_isolation_idempotency_and_validation(self):
        self.account();access=self.login();self.bearer(access)
        response=self.client.post("/api/customer/v1/media",{"file":self.image()},format="multipart",HTTP_IDEMPOTENCY_KEY="same")
        self.assertEqual(response.status_code,201,response.content)
        asset=response.json();url="/api/customer/v1/media/"+asset["id"]+"/download"
        self.assertEqual(self.client.post("/api/customer/v1/media",{"file":self.image()},format="multipart",HTTP_IDEMPOTENCY_KEY="same").status_code,200)
        self.assertEqual(MediaAsset.objects.count(),1)
        self.assertEqual(self.client.get(url).status_code,200)
        other=APIClient(enforce_csrf_checks=True);self.csrf(other);self.account("other@example.test");self.bearer(self.login("other@example.test",other),other)
        self.assertEqual(other.get(url).status_code,404)
        self.assertEqual(other.get("/api/public/v1/media/"+asset["id"]+"/download").status_code,404)
        for name,content in [("bad.svg",b'<svg onload="alert(1)"/>'),("fake.png",b'not a png'),("big.pdf",b'%PDF-'+b'x'*(10*1024*1024))]:
            r=self.client.post("/api/customer/v1/media",{"file":SimpleUploadedFile(name,content)},format="multipart",HTTP_IDEMPOTENCY_KEY=uuid4().hex)
            self.assertEqual(r.status_code,400,r.content);self.assertIn("file",r.json()["field_errors"])
    def test_space_deactivation_and_logout(self):
        self.account();access=self.login();self.bearer(access)
        self.space.active=False;self.space.save()
        self.assertEqual(self.client.get("/api/customer/v1/me").status_code,401)
        self.space.active=True;self.space.save()
        self.post("logout-all",{})
        self.assertEqual(self.client.get("/api/customer/v1/me").status_code,401)
    def test_password_reset_revokes_all_sessions(self):
        self.account();access=self.login();self.post("password-reset/request",{"space":"main","email":"ahmed@example.test"},202)
        row=IdentityChallenge.objects.get(purpose="reset")
        self.post("password-reset/confirm",{"challenge_id":str(row.pk),"token":challenge_token(row),"password":"Another-Long-Password!156"})
        self.bearer(access);self.assertEqual(self.client.get("/api/customer/v1/me").status_code,401)
    def test_staff_space_scope_and_module_disable(self):
        staff=get_user_model().objects.create_user(username="staff")
        role=Role.objects.create(name="foundation_test",permissions=["publication.view","publication.manage"])
        Assignment.objects.create(user=staff,role=role,site=self.site)
        c=Client();c.force_login(staff)
        self.assertEqual(len(c.get("/api/platform/v1/spaces").json()["results"]),1)
        other=Site.objects.create(name="West");PublicationSpace.objects.create(code="other",name="Other",internal_site=other)
        self.assertEqual(len(c.get("/api/platform/v1/spaces").json()["results"]),1)
        ModuleState.objects.filter(module_id="public_identity").update(enabled=False)
        self.assertEqual(self.client.get("/api/public/v1/auth/csrf").status_code,404)
    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_confirmation_delivery_and_no_token_in_database(self):
        from django.core import mail
        from .tasks import deliver_challenge
        self.account()
        row=IdentityChallenge.objects.get();row.consumed_at=None;row.lease=uuid4();row.save()
        deliver_challenge(str(row.pk),str(row.lease))
        self.assertEqual(len(mail.outbox),1)
        self.assertIn("/public/auth/confirm#",mail.outbox[0].body)
        row.refresh_from_db();self.assertIsNotNone(row.sent_at)
        self.assertNotEqual(row.token_hash,challenge_token(row))

    def test_staff_session_cannot_impersonate_customer_and_wrong_audience_rejected(self):
        from .security import decode,backend
        staff=get_user_model().objects.create_superuser(username="same-name-not-a-customer")
        c=APIClient();c.force_login(staff)
        self.assertEqual(c.get("/api/customer/v1/me").status_code,401)
        self.account();access=self.login()
        claims=decode(access,"access")
        from rest_framework_simplejwt.backends import TokenBackend
        from django.conf import settings
        wrong=TokenBackend(algorithm="HS256",signing_key=settings.ERP_CUSTOMER_JWT_KEY,audience="erpi.staff",issuer="erpi.public").encode(claims)
        self.bearer(wrong);self.assertEqual(self.client.get("/api/customer/v1/me").status_code,401)
    def test_registration_persists_when_broker_unavailable(self):
        from unittest.mock import patch
        from kombu.exceptions import OperationalError
        from .tasks import dispatch
        self.post("register",{"space":"main","email":"queued@example.test","password":self.secret,"display_name":"Queued"},202)
        with patch("backbone.celeryapp.app.send_task",side_effect=OperationalError("broker down")):
            self.assertEqual(dispatch(),0)
        row=IdentityChallenge.objects.get();self.assertIsNone(row.sent_at)
        self.assertEqual(row.attempts,1)
        self.assertEqual(ExternalIdentity.objects.get().status,"pending")

@override_settings(DEBUG=True)
class BusinessPortalTests(TransactionTestCase):
    setUp=FoundationTests.setUp
    csrf=FoundationTests.csrf
    post=FoundationTests.post
    account=FoundationTests.account
    login=FoundationTests.login
    bearer=FoundationTests.bearer

    def test_private_documents_and_booking_retry(self):
        from datetime import timedelta,time
        from scheduling.models import Resource,Appointment
        from billing_core.models import Invoice,Receipt
        from parties.models import Party
        for module in ('parties','scheduling','billing_core'):
            ModuleState.objects.update_or_create(module_id=module,defaults={'enabled':True})
        account=self.account()
        scope=dict(space=self.space,site=self.site)
        party=Party.objects.create(**scope,name_en='Client',name_ar='عميل',party_type='customer',account=account)
        account.party=party
        account.save(update_fields=["party"])
        other=Party.objects.create(**scope,party_type='contact')
        self.bearer(self.login())
        root='/api/customer/v1/business/'
        res=Resource.objects.create(**scope,name='Room',timezone='UTC',weekdays=list(range(7)),opens=time(9),closes=time(12),slot_minutes=30)
        day=(timezone.now()+timedelta(days=2)).date()
        slots=self.client.get(root+f'resources/{res.pk}/slots?date={day}').json()['results']
        self.assertEqual(len(slots),6)
        data=dict(resource_id=res.pk,start=slots[0],party_id=other.pk)
        key=str(uuid4())
        for status in (201,200):
            r=self.client.post(root+'appointments',data,format='json',HTTP_IDEMPOTENCY_KEY=key)
            self.assertEqual(r.status_code,status,r.content)
        self.assertEqual(Appointment.objects.get().party,party)
        self.assertEqual(self.client.post(root+'appointments',data,format='json',HTTP_IDEMPOTENCY_KEY=str(uuid4())).status_code,409)
        params=dict(**scope,currency='AED',subtotal=10,total=10,tax=0)
        own=Invoice.objects.create(**params,party=party,number='A',status='issued')
        foreign=Invoice.objects.create(**params,party=other,number='B',status='issued')
        draft=Invoice.objects.create(**params,party=party,number='C',status='draft')
        self.assertEqual([r['id'] for r in self.client.get(root+'invoices').json()['results']],[own.pk])
        for obj,status in [(own,200),(foreign,404),(draft,404)]:
            self.assertEqual(self.client.get(root+f'invoices/{obj.pk}/print').status_code,status)
        receipt=Receipt.objects.create(**scope,invoice=foreign,number='R1',amount=5)
        self.assertEqual(self.client.get(root+f'receipts/{receipt.pk}/print').status_code,404)
        self.assertEqual(Client().get(root+'appointments').status_code,401)

    def test_media_folder_boundaries(self):
        from media_service.models import MediaFolder
        from PIL import Image
        from io import BytesIO
        account=self.account();self.bearer(self.login())
        root='/api/customer/v1/media'
        r=self.client.post(root+'/folders',{'name':'Visits'},format='json')
        self.assertEqual(r.status_code,201,r.content)
        pk=r.json()['id']
        self.assertEqual(self.client.patch(root+'/folders/'+pk,{'revision':1,'name':'Receipts'},format='json').json()['name'],'Receipts')
        self.assertEqual(self.client.post(root+'/folders',{'name':'receipts'},format='json').status_code,409)
        other=self.account('another@example.test')
        foreign=MediaFolder.objects.create(name='Foreign',space=self.space,external_owner=other)
        site2=PublicationSpace.objects.create(code='isolated',name='Isolated',internal_site=self.site)
        cross=MediaFolder.objects.create(name='Cross',space=site2,external_owner=account)
        raw=BytesIO();Image.new('RGB',(200,80),'blue').save(raw,format='PNG')
        uploaded=self.client.post(root,{'file':SimpleUploadedFile('sample.png',raw.getvalue(),content_type='image/png')},format='multipart',HTTP_IDEMPOTENCY_KEY='test-folder-image')
        self.assertEqual(uploaded.status_code,201,uploaded.content)
        url=root+'/'+uploaded.json()['id']
        for invalid in (foreign.pk,cross.pk):
            self.assertEqual(self.client.patch(url,{'folder_id':str(invalid)},format='json').status_code,404)
            self.assertEqual(self.client.get(root,{'folder':str(invalid)}).status_code,404)
        self.assertEqual(self.client.patch(url,{'folder_id':pk},format='json').status_code,200)
        self.assertEqual(self.client.get(root,{'folder':pk,'q':'sample','page_size':10}).json()['count'],1)
        thumb=self.client.get(url+'/thumbnail')
        self.assertEqual(thumb.status_code,200)
        self.assertEqual(thumb['Cache-Control'],'private, no-store')
        self.assertEqual(Image.open(BytesIO(thumb.content)).size,(160,64))
        self.assertIn('sandbox',thumb['Content-Security-Policy'])
        self.assertIn("img-src 'self' data: blob:",self.client.get('/public/account')['Content-Security-Policy'])
        self.assertEqual(self.client.delete(root+'/folders/'+pk,{},format='json').status_code,204)
        self.assertEqual(self.client.get(root,{'folder':'unfiled'}).json()['count'],1)
