from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone
from datetime import timedelta

from backbone.mfa import VERIFIED, stamp
from backbone.models import Assignment, AuditEvent, Authenticator, Delegation, ModuleState, Role, Site


class PurchaseTests(TestCase):
    def setUp(self):
        users = get_user_model().objects
        self.admin = users.create_superuser(username="admin")
        self.client.force_login(self.admin)
        device = Authenticator.objects.create(user=self.admin, encrypted_key="test-session-only")
        session = self.client.session
        session[VERIFIED] = stamp(device)
        session.save()
        self.east = Site.objects.create(name="East")
        self.west = Site.objects.create(name="West")
        self.manager_role = Role.objects.create(name="Manager", permissions=["purchasing.view", "purchasing.approve"])
        self.finance_role = Role.objects.create(name="Finance", permissions=["purchasing.view", "purchasing.approve"])
        self.manager, self.assignment = self.reviewer("manager", self.manager_role, self.east)
        self.finance, _ = self.reviewer("finance", self.finance_role, self.east)
        self.outsider, _ = self.reviewer("outsider", self.manager_role, self.west)
        ModuleState.objects.create(module_id="purchasing", enabled=True)

    def reviewer(self, name, role, site):
        user = get_user_model().objects.create_user(username=name)
        assignment = Assignment.objects.create(user=user, role=role, site=site)
        client = Client()
        client.force_login(user)
        return client, assignment

    def post(self, path, payload, client=None, **headers):
        return (client or self.client).post(path, payload, content_type="application/json", **headers)

    def publish(self, threshold):
        response = self.post("/api/purchase-templates", {"currency": "AED", "threshold": threshold,
                             "manager_role_id": self.manager_role.pk, "finance_role_id": self.finance_role.pk})
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def purchase(self, price, key="test", quantity="1"):
        response = self.post("/api/purchase-requests", {
            "title": "Pump seal", "site_id": self.east.pk, "currency": "AED",
            "lines": [{"description": "Seal", "quantity": quantity, "unit_price": price}],
        }, HTTP_IDEMPOTENCY_KEY=key)
        self.assertIn(response.status_code, (200, 201), response.content)
        return response.json()

    def action(self, row, action, client=None, reason=""):
        return self.post(f"/api/purchase-requests/{row['id']}/{action if action == 'submit' else 'review'}",
                         {"revision": row["revision"], "action": action, "reason": reason}, client)

    def test_threshold_rounding_and_version_pinning(self):
        self.publish("100.00")
        for index, price in enumerate(("33.3316", "33.3333", "33.3350")):
            draft = self.purchase(price, key=f"request-{index}", quantity="3")
            self.assertEqual(draft["total"], ("99.99", "100.00", "100.01")[index])
            row = self.action(draft, "submit").json()
            self.assertEqual(len(row["reviews"][0]["steps"]), 2 if index == 2 else 1)
            self.assertEqual(self.action(row, "approve", self.finance).status_code, 403)
            self.assertEqual(self.action(row, "approve", self.outsider).status_code, 404)
            after = self.action(row, "approve", self.manager).json()
            self.assertEqual(after["status"], "in_review" if index == 2 else "approved")
        self.publish("200.00")
        self.assertEqual(self.action(after, "approve", self.finance).json()["reviews"][0]["version"], 1)
        row = self.action(self.purchase("100.01", key="new"), "submit").json()
        self.assertEqual(row["reviews"][0]["version"], 2)
        self.assertEqual(len(row["reviews"][0]["steps"]), 1)

    def test_return_replay_and_delegation_audit(self):
        self.publish("100.00")
        draft = self.purchase("50.00")
        self.assertEqual(self.purchase("50.00")["id"], draft["id"])
        row = self.action(draft, "submit").json()
        self.assertEqual(self.action(row, "return", self.manager).status_code, 400)
        returned = self.action(row, "return", self.manager, "Confirm the quantity").json()
        self.assertEqual(returned["status"], "draft")
        self.assertEqual(self.action(row, "approve", self.manager).status_code, 409)
        row = self.action(returned, "submit").json()
        delegate = get_user_model().objects.create_user(username="alternate")
        alternate = Client()
        alternate.force_login(delegate)
        now = timezone.now()
        grant = Delegation.objects.create(
            requester=self.assignment.user, delegate=delegate, source_assignment=self.assignment,
            role=self.manager_role, site=self.east, permissions=["purchasing.view", "purchasing.approve"],
            starts_at=now - timedelta(minutes=1), ends_at=now + timedelta(minutes=1), status="approved",
        )
        self.assertEqual(alternate.get("/api/inbox").json()["items"][0]["number"], row["number"])
        self.assertEqual(self.outsider.get("/api/search?q=Pump").json()["items"], [])
        self.assertEqual(alternate.get("/api/search?q=Pump").json()["items"][0]["number"], row["number"])
        notices = alternate.get("/api/notifications").json()
        self.assertEqual(notices["unread"], 1)
        key = notices["items"][0]["key"]
        self.assertEqual(self.post("/api/notifications", {"key": key}, alternate).json()["unread"], 0)
        self.assertEqual(self.post("/api/notifications", {"key": key}, alternate).json()["unread"], 0)
        self.assertEqual(self.manager.get("/api/notifications").json()["unread"], 1)
        self.assertEqual(self.action(row, "approve", alternate).json()["reviews"][0]["decisions"][0]["delegation_id"], grant.pk)
        self.assertEqual(alternate.get("/api/inbox").json()["items"], [])
        self.assertEqual(self.manager.get("/api/inbox").json()["items"], [])
        self.assertEqual(self.client.get("/api/notifications").json()["items"][0]["event"], "approve")
        self.assertEqual(self.post("/api/notifications", {"all": True}).json()["unread"], 0)
        grant.ends_at = now - timedelta(seconds=1)
        grant.save(update_fields=["ends_at"])
        self.assertEqual(alternate.get("/api/search?q=Pump").json()["items"], [])
        self.assertTrue(AuditEvent.objects.filter(actor="alternate", target__endswith=f"delegation #{grant.pk}").exists())

    def test_module_gating_and_currency_template(self):
        draft = self.purchase("10.00")
        self.assertEqual(self.action(draft, "submit").json()["error"], "purchase_template_missing")
        ModuleState.objects.filter(pk="purchasing").update(enabled=False)
        self.assertEqual(self.client.get("/api/purchase-requests").status_code, 404)
