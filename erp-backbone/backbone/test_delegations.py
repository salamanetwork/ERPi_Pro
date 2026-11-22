from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from workorders.models import ApprovalRun, ApprovalTemplate, WorkOrder
from .mfa import VERIFIED, stamp
from .models import Assignment, AuditEvent, Authenticator, ModuleState, Role, Site


class DelegationTests(TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 13, 8, tzinfo=timezone.utc)
        self.clock = patch("django.utils.timezone.now", return_value=self.now)
        self.time = self.clock.start()
        self.addCleanup(self.clock.stop)
        users = get_user_model().objects
        self.admin = users.create_superuser(username="admin")
        self.manager = users.create_user(username="manager")
        self.delegate = users.create_user(username="alternate")
        self.submitter = users.create_user(username="operator")
        self.admin_client = self.login(self.admin)
        self.manager_client = self.login(self.manager)
        self.delegate_client = self.login(self.delegate)
        self.east = Site.objects.create(name="East")
        self.west = Site.objects.create(name="West")
        role = Role.objects.create(name="Review manager", permissions=[
            "platform.delegate", "workorders.view", "workorders.approve", "workorders.edit",
        ])
        self.assignment = Assignment.objects.create(user=self.manager, role=role, site=self.east)
        self.template = ApprovalTemplate.objects.create(version=1, steps=[{"id": role.pk, "name": role.name}])
        ModuleState.objects.create(module_id="workorders", enabled=True)

    def login(self, user):
        client = Client()
        client.force_login(user)
        if user.is_superuser:
            device = Authenticator.objects.create(user=user, encrypted_key="test-session-only")
            session = client.session
            session[VERIFIED] = stamp(device)
            session.save()
        return client

    def at(self, minutes):
        return self.now + timedelta(minutes=minutes)

    def post(self, client, path, data):
        return client.post(path, data, content_type="application/json")

    def request(self, **changes):
        return self.post(self.manager_client, "/api/delegations", {
            "assignment_id": self.assignment.pk, "delegate_id": self.delegate.pk, "site_id": self.east.pk,
            "permissions": ["workorders.view", "workorders.approve"], "reason": "Cover morning shift",
            "starts_at": self.at(10).isoformat(), "ends_at": self.at(20).isoformat(), **changes,
        })

    def decide(self, grant, action="approve", client=None):
        return self.post(client or self.admin_client, f"/api/delegations/{grant}/decision", {"action": action})

    def order(self, site, submitter=None):
        order = WorkOrder.objects.create(title="Review test", site=site, status="in_review")
        ApprovalRun.objects.create(order=order, template=self.template, submitted_by=submitter or self.submitter)
        return order

    def review(self, order):
        return self.post(self.delegate_client, f"/api/work-orders/{order.pk}/review", {
            "action": "approve", "revision": order.revision,
        })

    def test_scope_window_and_delegated_approval_audit(self):
        order = self.order(self.east)
        west = self.order(self.west)
        self_review = self.order(self.east, self.manager)
        response = self.request()
        self.assertEqual(response.status_code, 201, response.content)
        grant = response.json()["id"]
        self.assertEqual(self.decide(grant, client=self.manager_client).status_code, 403)
        self.assertEqual(self.decide(grant).json()["status"], "scheduled")
        self.assertEqual(self.review(order).status_code, 404)
        self.time.return_value = self.at(10)
        queue = self.delegate_client.get("/api/work-orders?review=mine").json()["items"]
        self.assertEqual([item["id"] for item in queue], [order.pk])
        self.assertEqual(self.review(west).status_code, 404)
        self.assertEqual(self.review(self_review).status_code, 403)
        result = self.review(order).json()
        self.assertEqual(result["status"], "approved")
        self.assertEqual(result["approval_runs"][0]["decisions"][0]["delegation_id"], grant)
        self.assertTrue(AuditEvent.objects.filter(actor="alternate",
                        target=f"WO-{order.pk} / v1 / delegation #{grant}").exists())
        self.time.return_value = self.at(20)
        self.assertEqual(self.delegate_client.get(f"/api/work-orders/{order.pk}").status_code, 404)
        self.assertEqual(self.delegate_client.get("/api/delegations").json()["items"][0]["status"], "expired")

    def test_extension_requires_approval_and_revoke_cancels_pending_extension(self):
        grant = self.request().json()["id"]
        self.decide(grant)
        data = {"previous": grant, "ends_at": self.at(35).isoformat(), "reason": "Shift extended"}
        response = self.post(self.manager_client, "/api/delegations", data)
        self.assertEqual(response.status_code, 201, response.content)
        extension = response.json()["id"]
        self.assertEqual(self.post(self.manager_client, "/api/delegations", data).status_code, 409)
        self.assertEqual(self.decide(grant, "revoke").json()["status"], "revoked")
        self.assertEqual(self.decide(extension).status_code, 409)

    def test_extension_starts_at_expiry_and_assignment_removal_invalidates_grant(self):
        grant = self.request().json()["id"]
        self.decide(grant)
        extension = self.post(self.manager_client, "/api/delegations", {
            "previous": grant, "ends_at": self.at(35).isoformat(), "reason": "Longer shift",
        }).json()["id"]
        self.decide(extension)
        self.time.return_value = self.at(20)
        order = self.order(self.east)
        self.assertEqual(self.delegate_client.get(f"/api/work-orders/{order.pk}").status_code, 200)
        self.assignment.delete()
        self.assertEqual(self.review(order).status_code, 404)

    def test_cannot_expand_scope_or_delegate_platform_permissions(self):
        self.assertEqual(self.request(site_id=self.west.pk).status_code, 403)
        self.assertEqual(self.request(permissions=["platform.audit"]).status_code, 400)
        self.assertEqual(self.request(delegate_id=self.manager.pk).status_code, 400)

    def test_recipient_cannot_redelegate_received_authority(self):
        from .models import Delegation
        grant = self.request().json()['id']
        self.decide(grant)
        self.time.return_value = self.at(12)
        direct = Role.objects.create(name='Delegation requests', permissions=['platform.delegate'])
        Assignment.objects.create(user=self.delegate, site=self.east, role=direct)
        response = self.post(self.delegate_client, '/api/delegations', {
            'assignment_id': self.assignment.pk, 'delegate_id': self.submitter.pk,
            'permissions': ['workorders.approve'], 'site_id': self.east.pk,
            'starts_at': self.at(12).isoformat(), 'ends_at': self.at(18).isoformat(), 'reason': 'Cover',
        })
        self.assertEqual(response.status_code, 400)
        self.assignment.delete()
        self.assertEqual(Delegation.objects.get(pk=grant).status, 'revoked')
