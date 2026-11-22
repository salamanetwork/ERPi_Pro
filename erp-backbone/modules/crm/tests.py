from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections
from django.test import Client, TransactionTestCase
from django.utils import timezone

from backbone.models import Assignment, AuditEvent, ModuleState, Role, Site
from inventory.models import Item, Unit
from sales.models import Customer, Document
from .models import CustomerNumber, Opportunity, QuotationLink, Stage, StageHistory


class CrmFlowTests(TransactionTestCase):
    def setUp(self):
        for name in ("crm", "sales", "customers"):
            ModuleState.objects.update_or_create(module_id=name, defaults={"enabled": True})
        for index, (code, kind) in enumerate((
            ("new", "open"), ("qualified", "open"), ("proposal", "open"),
            ("negotiation", "open"), ("won", "won"), ("lost", "lost"),
        )):
            Stage.objects.get_or_create(code=code, defaults={"name_ar": code, "name_en": code, "order": index + 1, "kind": kind})
        CustomerNumber.objects.get_or_create(pk=1)
        self.site = Site.objects.create(name="North sales")
        self.other = Site.objects.create(name="South sales")
        self.rep = self.make_user("rep", self.site, ["crm.view", "crm.manage", "sales.view", "sales.quote"])
        self.approver = self.make_user("approver", self.site, ["crm.view", "crm.customer_approve", "sales.view"])
        Assignment.objects.create(user=self.approver, role=Role.objects.create(name="Customer master", permissions=["sales.customer"]))
        self.outsider = self.make_user("outsider", self.other, ["crm.view", "crm.manage", "sales.view"])
        self.client.force_login(self.rep)
        self.item = Item.objects.create(code="TEST-CRM", name="Valve", base_unit=Unit.objects.get_or_create(code="EA", defaults={"name":"Each"})[0])

    def make_user(self, name, site, perms):
        user = get_user_model().objects.create_user(name)
        Assignment.objects.create(user=user, site=site, role=Role.objects.create(name=name, permissions=perms))
        return user

    def post(self, path, payload, key="test", client=None):
        return (client or self.client).post("/api/crm" + path, payload, content_type="application/json",
                                           HTTP_IDEMPOTENCY_KEY=key)

    def create_opportunity(self):
        lead = self.post("/leads", {"name": "Nadia", "site_id": self.site.pk}, "lead").json()
        reply = self.client.patch(f"/api/crm/leads/{lead['id']}", {"revision": 1, "name": "Nadia", "state": "qualified"},
                                  content_type="application/json")
        self.assertEqual(reply.status_code, 200, reply.content)
        result = self.post(f"/leads/{lead['id']}/convert", {
            "title": "Workshop", "revision": 2, "estimated_amount": "210.25", "currency": "AED",
        }, "conversion")
        self.assertEqual(result.status_code, 201, result.content)
        return result.json()

    def test_approval_sequence_and_portfolio_scope(self):
        item = self.create_opportunity()
        pk = item["id"]
        Customer.objects.create(code="CUS-000001", name="Existing manual code")
        request = self.post(f"/opportunities/{pk}/customer-requests", {"name": "New business", "revision": 1}).json()
        self.assertFalse(Customer.objects.filter(name="New business").exists())
        self.assertEqual(self.client.get("/api/workspace/dashboard").json()["crm"]["currencies"], {"AED": "210.25"})
        outsider = Client()
        outsider.force_login(self.outsider)
        self.assertEqual(outsider.get(f"/api/crm/opportunities/{pk}").status_code, 404)
        self.assertEqual(outsider.get("/api/workspace/dashboard").json()["crm"]["count"], 0)
        approver = Client()
        approver.force_login(self.approver)
        task = approver.get("/api/inbox").json()["items"][0]
        self.assertEqual(task["action"], "crm_customer_review")
        self.assertEqual(approver.get("/api/workspace/counters").json(), {"inbox": 1, "unread": 1})
        response = self.post(f"/customer-requests/{request['id']}/decision", {"revision": 1, "action": "approve"}, client=approver)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["customer_code"], "CUS-000002")
        self.assertEqual(approver.get("/api/workspace/counters").json(), {"inbox": 0, "unread": 0})
        self.assertEqual(Opportunity.objects.get(pk=pk).customer.name, "New business")
        self.assertTrue(AuditEvent.objects.filter(action="crm.customer.approve").exists())

    def test_due_activity_reminder_is_scoped_and_disappears_after_completion(self):
        item = self.create_opportunity()
        path = f"/opportunities/{item['id']}/transition"
        stage = Stage.objects.get(code="proposal")
        result = self.post(path, {"revision": 1, "stage_id": stage.pk})
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(self.post(path, {"revision": 1, "stage_id": stage.pk}).status_code, 409)
        self.assertEqual(StageHistory.objects.filter(opportunity_id=item["id"]).count(), 2)
        start = timezone.now()
        from unittest.mock import patch
        with patch("django.utils.timezone.now", return_value=start):
            row = self.post("/activities", {"opportunity_id": item["id"], "kind":"call", "title":"Follow up",
                                            "due_at": (start + timedelta(minutes=5)).isoformat()}).json()
            self.assertEqual(self.client.get("/api/workspace/counters").json(), {"inbox": 0, "unread": 0})
        with patch("django.utils.timezone.now", return_value=start + timedelta(minutes=5)):
            counters = self.client.get("/api/workspace/counters").json()
            self.assertEqual(counters, {"inbox": 1, "unread": 1})
            notice = self.client.get("/api/notifications").json()["items"][0]
            self.assertEqual(notice["event"], "crm_activity_due")
            self.client.post("/api/notifications", {"key": notice["key"]}, content_type="application/json")
            self.assertEqual(self.client.get("/api/workspace/counters").json(), {"inbox": 1, "unread": 0})
            response = self.client.patch(f"/api/crm/activities/{row['id']}", {"action":"complete","revision":1},
                                         content_type="application/json")
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(self.client.get("/api/workspace/counters").json(), {"inbox": 0, "unread": 0})

    def test_quote_concurrency_and_idempotent_replay(self):
        item = self.create_opportunity()
        customer = Customer.objects.create(code="BUY-1", name="Buyer")
        path = f"/opportunities/{item['id']}"
        self.assertEqual(self.post(path+"/customer-link", {"revision":1,"customer_id":customer.pk}).status_code, 200)
        today = timezone.localdate()
        quote = {"revision":2,"date":today.isoformat(),"expires":(today+timedelta(days=14)).isoformat(),
                 "lines":[{"item_id":self.item.pk,"quantity":"2","price":"75"}]}
        barrier = Barrier(2)
        def submit(key):
            close_old_connections()
            try:
                client = Client()
                client.force_login(self.rep)
                barrier.wait(timeout=10)
                result = self.post(path+"/quotation", quote, key, client=client)
                return key, result.status_code, result.json()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(submit, ("first", "second")))
        self.assertEqual(sorted(result[1] for result in responses), [201,409], responses)
        key, _, accepted = next(result for result in responses if result[1] == 201)
        self.assertEqual(self.post(path+"/quotation", quote, key).json(), accepted)
        self.assertEqual(self.post(path+"/quotation", {**quote,"lines":[]}, key).status_code, 409)
        self.assertEqual(Document.objects.filter(kind="quote").count(), 1)
        self.assertEqual(QuotationLink.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action="crm.quotation.create").count(), 1)
