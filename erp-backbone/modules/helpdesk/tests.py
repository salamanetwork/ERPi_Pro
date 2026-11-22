import io
import json
from datetime import timedelta
from unittest.mock import Mock, patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from backbone.mfa import VERIFIED, stamp
from backbone.models import Assignment, Authenticator, ExperienceNotice, ModuleState, NotificationDelivery, Role, Site
from backbone.registry import MANIFESTS
from backbone.services.outbox import claim, pump
from backbone.tasks import deliver_notification

from .models import Ticket, TicketEvent
from .sla import schedule_overdue

BASE = "/api/helpdesk/tickets"


class HelpdeskTests(TestCase):
    def setUp(self):
        ModuleState.objects.update_or_create(module_id="helpdesk", defaults={"enabled":True})
        self.site = Site.objects.create(name="Main")
        self.other_site = Site.objects.create(name="Other")
        self.requester, self.client = self.account("requester", self.site, "request")
        self.peer, self.peer_client = self.account("peer", self.site, "request")
        self.agent, self.agent_client = self.account("agent", self.site, "agent", "reopen")
        self.dispatcher, self.dispatch_client = self.account("dispatcher", self.site, "dispatch", "configure")
        self.other, self.other_client = self.account("outsider", self.other_site, "agent", "dispatch")
        self.open_values = {"site_id":self.site.pk,"subject":"Fan failed","description":"Device unavailable","priority":"urgent"}

    def account(self, name, site, *permissions):
        user = get_user_model().objects.create_user(username=name)
        role = Role.objects.create(name=name,permissions=["helpdesk.view",*["helpdesk."+key for key in permissions]])
        Assignment.objects.create(user=user, role=role, site=site)
        client = Client()
        client.force_login(user)
        return user, client

    def post(self, client, path, values, code=201, key=None):
        response = client.post(path, values, content_type="application/json", HTTP_IDEMPOTENCY_KEY=key or uuid4().hex)
        self.assertEqual(response.status_code,code,response.content)
        return response.json()

    def open(self):
        return self.post(self.client,BASE,self.open_values)

    def test_ticket_survives_redis_outage_and_delivery_replay(self):
        with patch("backbone.services.outbox.redis.Redis.from_url", side_effect=AssertionError("Request called broker")):
            ticket=self.post(self.client,BASE,self.open_values,key="one")
            self.assertEqual(ticket,self.post(self.client,BASE,self.open_values,200,"one"))
        self.assertEqual(Ticket.objects.count(),1)
        self.assertEqual(NotificationDelivery.objects.get().recipient,self.dispatcher)
        with override_settings(CELERY_BROKER_URL="redis://127.0.0.1:1/0"):
            ready = Mock()
            self.assertEqual(pump(on_ready=ready),0)
            ready.assert_not_called()
        url="/api/workspace/notifications/status"
        self.assertTrue(self.dispatch_client.get(url).json()["delayed"])
        self.assertEqual(self.other_client.get(url).json()["pending"],0)
        pk,token=claim()
        deliver_notification.run(pk,str(token))
        deliver_notification.run(pk,str(token))
        self.assertEqual(ExperienceNotice.objects.filter(user=self.dispatcher,kind="helpdesk").count(),1)
        items=self.dispatch_client.get("/api/notifications").json()["items"]
        self.assertEqual(items[0]["route"],"/helpdesk/"+str(ticket["id"]))
        self.assertEqual(self.dispatch_client.get(url).json()["pending"],0)

    def test_lifecycle_sla_private_comments_and_revision(self):
        opened=self.open()
        key=opened["id"]
        path=f"{BASE}/{key}"
        self.assertEqual(self.peer_client.get(path).status_code,404)
        self.assertEqual(self.other_client.get(path).status_code,404)
        self.assertEqual(self.other_client.get(BASE).json()["count"],0)
        assigned=self.post(self.dispatch_client,path+"/assign",{"revision":1,"assignee_id":self.agent.pk})
        self.post(self.dispatch_client,path+"/assign",{"revision":1,"assignee_id":self.agent.pk},409)
        self.post(self.client,path+"/comments",{"body":"No","internal":True},403)
        self.post(self.agent_client,path+"/comments",{"body":"Private","internal":True})
        self.assertIsNone(Ticket.objects.get(pk=key).first_response_at)
        started=self.post(self.agent_client,path+"/transition",{"revision":assigned["revision"],"status":"in_progress"})
        self.post(self.agent_client,path+"/comments",{"body":"Public response","internal":False})
        ticket=Ticket.objects.get(pk=key)
        self.assertIsNotNone(ticket.first_response_at)
        self.assertEqual(ticket.first_response_due-ticket.created_at,timedelta(minutes=30))
        self.assertEqual(ticket.revision,started["revision"]+1)
        self.assertEqual([row["body"] for row in self.client.get(path+"/comments").json()["results"]],["Public response"])
        self.assertEqual(self.agent_client.get(path+"/comments").json()["count"],2)
        resolved=self.post(self.agent_client,path+"/transition",{"revision":ticket.revision,"status":"resolved"})
        closed=self.post(self.client,path+"/transition",{"revision":resolved["revision"],"status":"closed"})
        self.post(self.agent_client,path+"/transition",{"revision":closed["revision"],"status":"in_progress"},400)
        self.post(self.agent_client,path+"/transition",{"revision":closed["revision"],"status":"in_progress","reason":"Recurring"})
        self.assertTrue(TicketEvent.objects.filter(reason="Recurring").exists())

    def test_sla_changes_apply_only_to_new_tickets_and_alert_once(self):
        old=self.open()
        self.post(self.dispatch_client,"/api/helpdesk/sla-policies",{
            "site_id":self.site.pk,"priority":"urgent","first_response_minutes":10,"resolution_minutes":20})
        new=self.open()
        self.assertEqual(Ticket.objects.get(pk=old["id"]).first_response_due - Ticket.objects.get(pk=old["id"]).created_at,timedelta(minutes=30))
        item=Ticket.objects.get(pk=new["id"])
        self.assertEqual(item.first_response_due-item.created_at,timedelta(minutes=10))
        Ticket.objects.filter(pk=item.pk).update(first_response_due=timezone.now()-timedelta(seconds=1))
        schedule_overdue()
        schedule_overdue()
        self.assertEqual(TicketEvent.objects.filter(ticket=item,event_type="first_response_overdue").count(),1)
        self.assertEqual(NotificationDelivery.objects.filter(event__event="helpdesk_sla_first_response").count(),1)

    def test_audit_and_outbox_epoch_roll_back_with_ticket(self):
        with self.assertRaisesMessage(RuntimeError,"forced rollback"):
            with transaction.atomic():
                self.open()
                raise RuntimeError("forced rollback")
        self.assertEqual(Ticket.objects.count(),0)
        self.assertEqual(TicketEvent.objects.count(),0)
        self.assertEqual(NotificationDelivery.objects.count(),0)

    def test_revoked_access_skips_pending_delivery(self):
        self.open()
        Assignment.objects.filter(user=self.dispatcher).delete()
        pk,token=claim()
        deliver_notification.run(pk,str(token))
        self.assertEqual(NotificationDelivery.objects.get(pk=pk).state,"skipped")

    def test_preflight_blocks_enabled_incompatible_module_and_activation(self):
        name="helpdesk"
        manifest={**MANIFESTS[name],"core_version_range":">=99"}
        with patch.dict(MANIFESTS,{name:manifest}):
            with self.assertRaisesMessage(CommandError,"helpdesk: core_version"):
                call_command("erp_preflight",stdout=io.StringIO())
            ModuleState.objects.filter(module_id=name).update(enabled=False)
            call_command("erp_preflight",stdout=io.StringIO())
            admin=get_user_model().objects.create_superuser(username="admin")
            web=Client()
            web.force_login(admin)
            device=Authenticator.objects.create(user=admin,encrypted_key="test-only")
            session=web.session
            session[VERIFIED]=stamp(device)
            session.save()
            attempt=web.patch(f"/api/modules/{name}",json.dumps({"enabled":True}),content_type="application/json")
            self.assertEqual(attempt.status_code,409,attempt.content)
            self.assertEqual(attempt.json()["error"],"incompatible_module")

    def test_optional_project_link_enforces_membership(self):
        from projects.models import Membership, Project, Task

        ModuleState.objects.update_or_create(module_id="projects",defaults={"enabled":True})
        for user in (self.dispatcher,self.requester):
            role = Assignment.objects.get(user=user).role
            role.permissions = [*role.permissions, "projects.view"]
            role.save(update_fields=["permissions"])
        today = timezone.localdate()
        project = Project.objects.create(site=self.site,code="NO-LEAK",name="Restricted",start=today,end=today)
        task = Task.objects.create(project=project,title="Restricted task",due=today,planned_hours=0)
        ticket = self.open()
        path = f"{BASE}/{ticket['id']}"
        payload = {"revision":1,"type":"project_task","object_id":task.pk}
        self.post(self.dispatch_client,path+"/links",payload,404)
        Membership.objects.create(project=project,user=self.dispatcher,role="member")
        self.post(self.dispatch_client,path+"/links",payload)
        self.assertEqual(self.dispatch_client.get(path).json()["links"][0]["label"],"Restricted task")
        self.assertEqual(self.client.get(path).json()["links"],[])

    def test_superuser_requester_receives_only_public_content(self):
        admin = get_user_model().objects.create_superuser(username="admin")
        web = Client()
        web.force_login(admin)
        device = Authenticator.objects.create(user=admin, encrypted_key="test-only")
        session = web.session
        session[VERIFIED] = stamp(device)
        session.save()

        ticket = self.post(web, BASE, self.open_values)
        path = f"{BASE}/{ticket['id']}"
        self.post(self.dispatch_client, path+"/comments", {"body":"Support only","internal":True})
        self.post(self.agent_client, path+"/comments", {"body":"Public update","internal":False})
        # Ownership continues to restrict this ticket even with a support role.
        role = Assignment.objects.get(user=self.agent).role
        Assignment.objects.create(user=admin, role=role, site=self.site)

        comments = web.get(path+"/comments", {"page_size":1}).json()
        self.assertEqual(comments["count"], 1)
        self.assertEqual([row["body"] for row in comments["results"]], ["Public update"])
        self.assertEqual(web.get(path+"/events").json()["count"], 2)
        self.assertFalse(web.get(path).json()["can_use_internal_notes"])
        self.post(web, path+"/comments", {"body":"Requester cannot add a note","internal":True}, 403)
        for support in (self.agent_client, self.dispatch_client):
            self.assertTrue(support.get(path).json()["can_use_internal_notes"])
            self.assertEqual(support.get(path+"/comments").json()["count"], 2)
            self.assertEqual(support.get(path+"/events").json()["count"], 3)

    def test_superuser_needs_explicit_support_role_in_ticket_scope(self):
        admin = get_user_model().objects.create_superuser(username="admin")
        web = Client()
        web.force_login(admin)
        device = Authenticator.objects.create(user=admin, encrypted_key="test-only")
        session = web.session
        session[VERIFIED] = stamp(device)
        session.save()

        ticket = self.open()
        path = f"{BASE}/{ticket['id']}"
        note = {"body":"Restricted", "internal":True}
        self.post(self.agent_client, path+"/comments", note)
        self.assertEqual(web.get(path+"/comments").json()["count"], 0)
        role = Assignment.objects.get(user=self.dispatcher).role
        Assignment.objects.create(user=admin, role=role, site=self.other_site)
        self.assertEqual(web.get(path+"/comments").json()["count"], 0)
        self.post(web, path+"/comments", note, 403)
        grant = Assignment.objects.create(user=admin, role=role, site=None)
        self.assertEqual(web.get(path+"/comments").json()["count"], 1)
        self.post(web, path+"/comments", note, 201, key="before-revocation")
        grant.delete()
        self.post(web, path+"/comments", note, 403, key="before-revocation")
        self.assertEqual(web.get(path+"/comments").json()["count"], 0)
