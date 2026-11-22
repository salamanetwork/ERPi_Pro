from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import DatabaseError, transaction
from django.test import Client, TestCase
from django.utils import timezone

from backbone.models import Assignment, ExperienceNotice, ModuleState, Role, Site
from inventory.models import SiteSettings as InventorySiteSettings

from . import models as m, services as s
from .feedback import record, timezone_alerts
from .providers import ProviderFailure, mail

BASE="/api/marketing"


class MarketingTests(TestCase):
    def setUp(self):
        self.site=Site.objects.create(name="Marketing site")
        ModuleState.objects.update_or_create(module_id="marketing",defaults={"enabled":True})
        self.editor=self.account("editor","marketing.editor")
        self.admin=self.account("approver","marketing.admin")
        self.address="ahmed@example.test"

    def account(self,name,role):
        user=get_user_model().objects.create_user(username=name)
        client=Client()
        client.force_login(user)
        Assignment.objects.create(user=user,role=Role.objects.get(name=role),site=self.site)
        return client

    def post(self,client,path,data,status=201,key=None):
        response=client.post(BASE+path,data,content_type="application/json",HTTP_IDEMPOTENCY_KEY=key or uuid4().hex)
        self.assertEqual(response.status_code,status,response.content)
        return response.json()

    def contact(self,name="Ahmed",address=None):
        key=self.post(self.editor,"/contacts",{"site_id":self.site.pk,"first_name":name,
            "channel":"email","address":address or self.address})["id"]
        return m.Contact.objects.get(pk=key)

    def opt(self,contact,channel="email",change="in"):
        return self.post(self.editor,f"/contacts/{contact.pk}/consents",{"endpoint_id":contact.endpoints.get(channel=channel).pk,
            "purpose":"marketing","change":change,"source":"documented"})

    def campaign(self,contacts,version=None):
        segments=[]
        for index in range(2):
            segments.append(self.post(self.editor,"/segments",{"site_id":self.site.pk,"name":f"Segment {index}",
                "contact_ids":[c.pk for c in contacts]})["id"])
        if not version:
            version=self.post(self.editor,"/templates",{"site_id":self.site.pk,"name":"Template","channel":"email",
                "subject":"Hello {{first_name}}","body":"Example for {{first_name}}"})["version"]
        data={"site_id":self.site.pk,"title":"Holiday","channel":"email","purpose":"marketing",
            "template_version_id":version,"segment_ids":segments}
        draft=self.post(self.editor,"/campaigns",data)
        return m.Campaign.objects.get(pk=draft["id"])

    def action(self,campaign,action,data=None,status=201):
        campaign.refresh_from_db()
        return self.post(self.admin,f"/campaigns/{campaign.pk}/{action}",{"revision":campaign.revision,**(data or {})},status)

    def send(self,delivery):
        lease=uuid4()
        m.Delivery.objects.filter(pk=delivery.pk).update(lease=lease,state="queued")
        with patch("marketing.services.rate_allowed",return_value=True):
            s.send_delivery.run(delivery.pk,str(lease))
        delivery.refresh_from_db()

    def test_double_opt_in_confirmation_and_expiry(self):
        m.SiteSettings.objects.create(site=self.site,consent_mode="double")
        contact=self.contact()
        self.assertEqual(self.opt(contact)["status"],"pending")
        token=m.Token.objects.get()
        self.assertAlmostEqual((token.expires_at-timezone.now()).total_seconds(),172800,delta=2)
        self.assertEqual(s.eligible(contact.endpoints.get(), "marketing"),"skipped_consent")
        path=BASE+"/confirm/"+s.token_value(token)
        self.assertEqual(Client().get(path).status_code,200)
        self.assertEqual(m.Consent.objects.get().status,"pending")
        self.assertEqual(Client().post(path).json()["status"],"confirmed")
        other=self.contact(address="else@example.test")
        self.opt(other)
        token=m.Token.objects.get(consent__endpoint__contact=other)
        m.Token.objects.filter(pk=token.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(Client().post(BASE+"/confirm/"+s.token_value(token)).status_code,410)
        self.assertEqual(m.Consent.objects.get(endpoint__contact=other).status,"expired")

    def test_preview_lock_forget_and_test_send(self):
        first=self.contact()
        second=self.contact(name="",address="missing@example.test")
        duplicate=self.contact(address=self.address)
        for contact in (first, second, duplicate):
            self.opt(contact)
        campaign=self.campaign([first,second,duplicate])
        preview=self.post(self.editor,f"/campaigns/{campaign.pk}/preview",{},200)
        self.assertEqual(len(preview["samples"]),3)
        self.assertEqual(preview["samples"][0]["subject"],"Hello Ahmed")
        self.assertEqual(preview["samples"][1]["missing_variables"],["first_name"])
        self.action(campaign,"approve")
        self.assertIsNotNone(m.TemplateVersion.objects.get(pk=campaign.template_version_id).locked_at)
        with self.assertRaises(DatabaseError),transaction.atomic():
            m.TemplateVersion.objects.filter(pk=campaign.template_version_id).update(body="mutated")
        self.action(campaign,"schedule",{"scheduled_at":timezone.now().isoformat()})
        self.assertEqual(campaign.recipients.count(),2)
        self.action(campaign,"test-send",{"address":"test@example.test","contact_id":first.pk})
        test=m.Delivery.objects.get(kind="test")
        with patch("marketing.providers.MailpitProvider.send",return_value=("test-id","delivered")) as provider:
            self.send(test)
        self.assertEqual(provider.call_count,1)
        self.assertEqual(str(provider.call_args.args[1]["Subject"]),"Hello Ahmed")
        self.assertIn("List-Unsubscribe-Post",provider.call_args.args[1])
        self.assertEqual(self.admin.get(f"{BASE}/campaigns/{campaign.pk}/metrics").json()["states"],{"pending":2})
        self.post(self.admin,f"/contacts/{first.pk}/forget",{"reason":"Verified request"})
        record=m.Delivery.objects.get(kind="campaign",endpoint__contact=first)
        self.assertEqual(record.state,"skipped_forgotten")
        self.assertEqual(record.address,"")
        self.assertEqual(test.endpoint.contact.pk,first.pk)
        self.assertFalse(m.ConsentHistory.objects.filter(endpoint__contact=first).exists())
        self.assertTrue(m.ConsentHistory.objects.filter(endpoint=None).exists())

    def test_site_timezone_drift_keeps_original_instant(self):
        contact=self.contact()
        self.opt(contact)
        tz=InventorySiteSettings.objects.create(site=self.site,timezone="Asia/Dubai")
        row=self.campaign([contact])
        self.action(row,"approve")
        self.action(row,"schedule",{"scheduled_at":"2027-02-01T10:00"})
        row.refresh_from_db()
        self.assertEqual(row.scheduled_at.hour,6)
        tz.timezone="Europe/London"
        tz.save(update_fields=["timezone"])
        timezone_alerts()
        timezone_alerts()
        self.assertTrue(self.admin.get(f"{BASE}/campaigns/{row.pk}").json()["timezone_changed"])
        self.assertEqual(ExperienceNotice.objects.filter(kind="marketing_timezone").count(),1)
        self.action(row,"schedule",{"scheduled_at":"2027-02-01T10:00"})
        row.refresh_from_db()
        self.assertEqual(row.scheduled_at.hour,10)

    def test_verified_event_effects_and_retry(self):
        contact=self.contact()
        self.opt(contact)
        campaign=self.campaign([contact])
        self.action(campaign,"approve")
        self.action(campaign,"schedule",{"scheduled_at":timezone.now().isoformat()})
        row=m.Delivery.objects.get(kind="campaign")
        with patch("marketing.providers.MailpitProvider.send",side_effect=ProviderFailure("429",retry=True,delay=90)):
            self.send(row)
        self.assertEqual(row.state,"pending")
        self.assertGreaterEqual((row.due_at-timezone.now()).total_seconds(),89)
        with patch("marketing.providers.MailpitProvider.send",return_value=("evt-ses","accepted")):
            self.send(row)
        record(row.provider,"delay","evt-ses","delay","MailboxFull")
        row.refresh_from_db()
        self.assertEqual(row.state,"accepted")
        record(row.provider,"soft","evt-ses","soft_bounce","MailboxFull")
        row.refresh_from_db()
        self.assertEqual(row.state,"pending")
        record(row.provider,"hard","evt-ses","hard_bounce","General")
        record(row.provider,"complaint","evt-ses","complaint","")
        record(row.provider,"complaint","evt-ses","complaint","")
        allowed=self.admin.get(BASE+"/suppressions").json()["results"]
        self.assertEqual({row["reason"] for row in allowed},{"hard_bounce","complaint"})
        self.assertTrue(all(row["created_at"] for row in allowed))
        self.assertEqual(ExperienceNotice.objects.filter(kind="marketing_complaint").count(),1)
        self.assertEqual(m.ProviderEvent.objects.count(),4)

    def test_campaign_actions_and_send_now_are_authorized_and_idempotent(self):
        contact=self.contact()
        self.opt(contact)
        campaign=self.campaign([contact])
        path=f"/campaigns/{campaign.pk}"
        data=self.admin.get(BASE+path).json()
        self.assertIn("approve",data["actions"])
        self.assertNotIn("send",data["actions"])
        editor_data=self.editor.get(BASE+path).json()
        self.assertEqual(editor_data["actions"],["preview"])
        self.post(self.editor,path+"/send",{"revision":1},404)
        self.action(campaign,"send",status=409)
        self.action(campaign,"approve")
        campaign.refresh_from_db()
        data=self.admin.get(BASE+path).json()
        self.assertIn("send",data["actions"])
        payload={"revision":campaign.revision}
        first=self.post(self.admin,path+"/send",payload,key="send-once")
        again=self.post(self.admin,path+"/send",payload,200,key="send-once")
        self.assertEqual(first,again)
        self.assertEqual(first["status"],"scheduled")
        self.assertEqual(campaign.recipients.count(),1)
        self.assertEqual(m.Delivery.objects.filter(kind="campaign").count(),1)
        self.assertLessEqual(m.Delivery.objects.get(kind="campaign").due_at,timezone.now())

    def test_creator_with_admin_role_sees_approval_explanation(self):
        user=get_user_model().objects.get(username="editor")
        Assignment.objects.create(user=user,role=Role.objects.get(name="marketing.admin"),site=self.site)
        contact=self.contact()
        row=self.campaign([contact])
        response=self.editor.get(BASE+f"/campaigns/{row.pk}").json()
        self.assertTrue(response["is_creator"])
        self.assertNotIn("approve",response["actions"])
        self.post(self.editor,f"/campaigns/{row.pk}/approve",{"revision":row.revision},403)
