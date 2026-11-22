from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from backbone.mfa import VERIFIED, stamp
from backbone.models import Assignment, AuditEvent, Authenticator, InboxTask, ModuleState, Role, Site
from .models import WorkOrder


class ApprovalTests(TestCase):
    def setUp(self):
        users = get_user_model().objects
        self.admin = users.create_superuser(username="admin")
        device = Authenticator.objects.create(user=self.admin, encrypted_key="test-session-only")
        self.client.force_login(self.admin)
        session = self.client.session
        session[VERIFIED] = stamp(device)
        session.save()
        self.site = Site.objects.create(name="East")
        other_site = Site.objects.create(name="West")
        self.first = Role.objects.create(name="Foreman", permissions=["workorders.view", "workorders.approve"])
        self.second = Role.objects.create(name="Engineer", permissions=["workorders.view", "workorders.approve"])
        self.reviewer = self.client_for(users.create_user(username="foreman"), self.first, self.site)
        self.engineer = self.client_for(users.create_user(username="engineer"), self.second, self.site)
        outsider = users.create_user(username="west")
        self.outsider = self.client_for(outsider, self.first, other_site)
        Assignment.objects.create(user=outsider, role=Role.objects.create(
            name="Read only", permissions=["workorders.view"]), site=self.site)
        Assignment.objects.create(user=self.admin, role=self.first, site=self.site)
        ModuleState.objects.create(module_id="workorders", enabled=True)

    def client_for(self, user, role, site):
        Assignment.objects.create(user=user, role=role, site=site)
        client = Client()
        client.force_login(user)
        return client

    def post(self, client, path, payload):
        return client.post(path, payload, content_type="application/json")

    def publish(self, *roles):
        response = self.post(self.client, "/api/work-order-approvals", {"role_ids": [role.pk for role in roles]})
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["version"]

    def make_order(self):
        row = WorkOrder.objects.create(title="Inspect pump", site=self.site)
        return f"/api/work-orders/{row.pk}"

    def submit(self, path, revision=1):
        return self.post(self.client, f"{path}/submit", {"revision": revision}).json()

    def decision(self, client, path, revision, action="approve", reason=""):
        return self.post(client, f"{path}/review", {"revision": revision, "action": action, "reason": reason})

    def queue(self, client):
        return [row["id"] for row in client.get("/api/work-orders?review=mine").json()["items"]]

    def test_version_pinning_and_scoped_sequential_review(self):
        self.assertEqual(self.publish(self.first, self.second), 1)
        path = self.make_order()
        pending = self.submit(path)
        self.assertEqual(pending["status"], "in_review")
        self.assertFalse(pending["can_review"])
        self.assertEqual(self.queue(self.reviewer), [pending["id"]])
        self.assertEqual(self.reviewer.get("/api/inbox").json()["items"][0]["kind"], "wo")
        self.assertEqual(self.queue(self.outsider), [])
        self.assertEqual(self.queue(self.engineer), [])
        for client in (self.client, self.engineer, self.outsider):
            self.assertEqual(self.decision(client, path, 2).status_code, 403)
        self.assertEqual(self.publish(self.second), 2)
        after = self.decision(self.reviewer, path, 2).json()
        self.assertEqual(after["approval_runs"][0]["version"], 1)
        self.assertEqual(self.queue(self.engineer), [pending["id"]])
        self.assertEqual(self.reviewer.get("/api/inbox").json()["items"], [])
        self.assertEqual(self.engineer.get("/api/inbox").json()["items"][0]["role"], "Engineer")
        self.assertEqual(self.decision(self.engineer, path, 2).status_code, 409)
        self.assertEqual(self.decision(self.engineer, path, 3).json()["status"], "approved")
        self.assertTrue(AuditEvent.objects.filter(action="workorder.approval.approve",
                                                  target=f"WO-{pending['id']} / v1").exists())
        fresh = self.submit(self.make_order())
        self.assertEqual(fresh["approval_runs"][0]["version"], 2)
        self.assertEqual(self.queue(self.engineer), [fresh["id"]])

    def test_return_reason_and_prior_submission_behavior(self):
        old = self.make_order()
        self.assertEqual(self.submit(old)["status"], "submitted")
        self.publish(self.first)
        path = self.make_order()
        self.submit(path)
        self.assertEqual(self.decision(self.reviewer, path, 2, "return").status_code, 400)
        returned = self.decision(self.reviewer, path, 2, "return", "Add the equipment tag.").json()
        self.assertEqual(returned["status"], "draft")
        self.assertEqual(returned["approval_runs"][0]["decisions"][0]["reason"], "Add the equipment tag.")
        self.publish(self.second)
        resent = self.submit(path, returned["revision"])
        self.assertEqual([run["version"] for run in resent["approval_runs"]], [2, 1])
        self.assertEqual(self.client.get(old).json()["status"], "submitted")
        self.assertEqual(self.post(self.reviewer, "/api/work-order-approvals",
                                   {"role_ids": [self.first.pk]}).status_code, 403)

    def test_site_assignment_routes_existing_order_to_inbox_and_notifications(self):
        site_a = Site.objects.create(name="Test Site A")
        site_b = Site.objects.create(name="Test Site B")
        manager = get_user_model().objects.create_user(username="manager_test")
        reviewer = self.client_for(manager, self.first, site_a)
        wrong_role = Assignment.objects.create(user=manager, role=self.second, site=site_b)
        self.publish(self.first)
        order = WorkOrder.objects.create(title="Inspect pump at Site B", site=site_b)
        path = f"/api/work-orders/{order.pk}"
        number = f"WO-{order.pk}"
        pending = self.submit(path)
        self.assertEqual(pending["status"], "in_review")
        self.assertEqual(pending["review_routing"], {
            "role_id": self.first.pk, "role_name": self.first.name, "eligible_reviewers": [],
        })
        self.assertTrue(InboxTask.objects.filter(kind="wo", object_id=order.pk, role=self.first).exists())
        self.assertEqual(reviewer.get("/api/inbox").json()["items"], [])
        self.assertEqual(reviewer.get("/api/notifications").json()["items"], [])
        self.assertFalse(reviewer.get(path).json()["can_review"])
        self.assertNotIn("review_routing", reviewer.get(path).json())

        # Existing pending runs remain actionable even without a legacy InboxTask row.
        InboxTask.objects.filter(kind="wo", object_id=order.pk).delete()
        Assignment.objects.create(user=manager, role=self.first, site=site_b)
        wrong_role.delete()
        self.assertEqual(self.client.get(path).json()["review_routing"]["eligible_reviewers"], ["manager_test"])
        self.assertEqual([item["number"] for item in reviewer.get("/api/inbox").json()["items"]], [number])
        self.assertEqual([item["number"] for item in reviewer.get("/api/notifications").json()["items"]], [number])
        self.assertEqual(self.queue(reviewer), [order.pk])
        self.assertTrue(reviewer.get(path).json()["can_review"])
        after = self.decision(reviewer, path, pending["revision"]).json()
        self.assertEqual(after["status"], "approved")
        self.assertNotIn("review_routing", self.client.get(path).json())
        self.assertEqual(reviewer.get("/api/inbox").json()["items"], [])
        self.assertEqual(reviewer.get("/api/notifications").json()["items"], [])
