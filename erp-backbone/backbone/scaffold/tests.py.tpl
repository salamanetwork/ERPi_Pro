from uuid import uuid4
from django.contrib.auth import get_user_model
from django.test import Client,TestCase
from backbone.models import Assignment,ModuleEventDelivery,ModuleState,OutboxEvent,Role,Site
from backbone.module_events import dispatch
from backbone import mfa
from backbone.models import Authenticator
from .models import EventReceipt,Record


class ContractTests(TestCase):
    def setUp(self):
        self.module="__SLUG__"
        self.scope="__SCOPE__"
        for key in (self.module,"publication"):
            ModuleState.objects.update_or_create(module_id=key,defaults={"enabled":True})
        self.a=Site.objects.create(name="Alpha")
        self.b=Site.objects.create(name="Beta")
        self.user=get_user_model().objects.create_user("module-operator")
        self.client=Client(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        device=Authenticator.objects.create(user=self.user,encrypted_key="test-session-stamp")
        session=self.client.session
        session[mfa.VERIFIED]=mfa.stamp(device)
        session.save()
        self.client.get("/admin/users")
        self.token=self.client.cookies["csrftoken"].value
        self.url="/api/"+self.module+"/records"
        self.role=Role.objects.create(name="Operator",permissions=[self.module+".view",self.module+".create"])

    def test_scope_and_durable_event(self):
        self.assertEqual(self.client.get(self.url).status_code,403)
        Assignment.objects.create(user=self.user,role=self.role,site=None if self.scope=="global" else self.a)
        read_url=self.url
        data={"name":"First record"}
        if self.scope!="global":
            a,b=self.a,self.b
            if self.scope=="space":
                from publication.models import PublicationSpace
                a=PublicationSpace.objects.create(internal_site=a,name="Space A",code="alpha")
                b=PublicationSpace.objects.create(internal_site=b,name="Space B",code="beta")
                read_url=self.url+"?space_id="+str(a.pk)
                same=PublicationSpace.objects.create(internal_site=self.a,name="Third space",code="third")
                Record.objects.create(name="Other space on same site",space=same)
                self.assertEqual(self.client.get(self.url).status_code,400)
                self.assertEqual(self.client.get(self.url+"?space_id="+str(b.pk)).status_code,403)
            Record.objects.create(name="Restricted",**{self.scope:b})
            data[self.scope+"_id"]=a.pk
            self.assertEqual(self.client.get(read_url).json()["count"],0)
            denied=self.client.post(self.url,{**data,self.scope+"_id":b.pk},content_type="application/json",
                HTTP_X_CSRFTOKEN=self.token,HTTP_IDEMPOTENCY_KEY=str(uuid4()))
            self.assertEqual(denied.status_code,403)
        headers={"content_type":"application/json","HTTP_X_CSRFTOKEN":self.token,"HTTP_IDEMPOTENCY_KEY":str(uuid4())}
        self.assertEqual(self.client.post(self.url,data,**headers).status_code,201)
        self.assertEqual(self.client.post(self.url,data,**headers).status_code,200)
        self.assertEqual(self.client.get(read_url).json()["count"],1)
        event=OutboxEvent.objects.get(module=self.module,event="created")
        self.assertTrue(ModuleEventDelivery.objects.filter(event_row=event,subscriber=self.module,completed_at=None).exists())
        from unittest.mock import patch
        from django.utils import timezone
        ModuleState.objects.filter(module_id=self.module).update(enabled=False)
        dispatch()
        self.assertFalse(EventReceipt.objects.filter(event=event).exists())
        ModuleState.objects.filter(module_id=self.module).update(enabled=True)
        with patch(self.module+".events.handle_event",side_effect=RuntimeError("retry test")):
            dispatch()
        delivery=ModuleEventDelivery.objects.get(event_row=event,subscriber=self.module)
        self.assertIsNone(delivery.completed_at)
        self.assertEqual(delivery.attempts,1)
        delivery.due_at=timezone.now()
        delivery.save(update_fields=["due_at"])
        dispatch()
        dispatch()
        self.assertEqual(EventReceipt.objects.filter(event=event).count(),1)
