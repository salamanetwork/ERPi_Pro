from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from importlib import import_module
from threading import Barrier
from unittest import skipUnless

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, connections, transaction
from django.test import Client, TransactionTestCase
from django.utils import timezone

from assets.models import Asset
from backbone.mfa import VERIFIED, stamp
from backbone.models import Assignment, Authenticator, ModuleState, Role, Site
from inventory.models import Item, Location, Position, Unit, Warehouse
from accounting.models import Posting
from .models import MaintenanceJob, MaintenancePlan, WorkOrder


class MaintenanceTests(TransactionTestCase):
    def setUp(self):
        for module in ("assets", "workorders", "inventory", "purchasing", "accounting"):
            ModuleState.objects.create(module_id=module, enabled=True)
        with connection.schema_editor() as editor:
            import_module("accounting.migrations.0002_default_ledger").seed(apps, editor)
            import_module("accounting.migrations.0007_maintenance_rule").seed(apps, editor)
        self.site = Site.objects.create(name="Test Site A")
        self.other = Site.objects.create(name="Test Site B")
        self.asset = Asset.objects.create(name="Pump", tag="P-01", site=self.site)
        self.unit, _ = Unit.objects.get_or_create(code="PC", defaults={"name": "Piece"})
        self.item = Item.objects.create(code="SEAL", name="Seal", base_unit=self.unit)
        self.location = Location.objects.create(code="A1", name="Shelf", warehouse=Warehouse.objects.create(
            site=self.site, code="WH", name="Warehouse"))
        self.admin = get_user_model().objects.create_superuser(username="admin")
        self.login_admin(self.client)
        self.technician = get_user_model().objects.create_user(username="tech")
        role = Role.objects.create(name="Technician", permissions=["workorders.view", "maintenance.view", "maintenance.execute"])
        Assignment.objects.create(user=self.technician, role=role, site=self.site)
        self.tech = Client()
        self.tech.force_login(self.technician)
        self.day = timezone.now().date().replace(day=1).isoformat()

    def login_admin(self, client):
        device, _ = Authenticator.objects.get_or_create(user=self.admin, defaults={"encrypted_key": "test"})
        client.force_login(self.admin)
        session = client.session
        session[VERIFIED] = stamp(device)
        session.save()

    def post(self, path, payload, client=None, key="test"):
        return (client or self.client).post(path, payload, content_type="application/json", HTTP_IDEMPOTENCY_KEY=key)

    def plan(self):
        payload = {"asset_id": self.asset.pk, "title": "Inspect pump", "first_due": "2026-01-31",
                   "frequency": "months", "interval": 1, "checklist": ["Inspect seals"]}
        result = self.post("/api/maintenance/plans", payload)
        self.assertEqual(result.status_code, 201, result.content)
        return result.json()

    def generate(self, plan, key, client=None):
        return self.post(f"/api/maintenance/plans/{plan['id']}/generate",
                         {"due_date": plan["first_due"]}, client, key)

    def activate(self):
        self.assertEqual(self.post("/api/accounting/periods", {"start": self.day}).status_code, 201)
        path = "/api/accounting/cutover"
        preview = self.post(path, {"cutoff": self.day}).json()
        self.assertEqual(self.post(path, {"cutoff": self.day, "token": preview["token"]}).status_code, 201)
        response = self.post("/api/inventory/opening", {"site_id": self.site.pk, "lines": [
            {"item_id": self.item.pk, "location_id": self.location.pk, "quantity": "1", "unit_cost": "100"}]})
        self.assertEqual(response.status_code, 201, response.content)

    def new_running_order(self, title, *, assign_center=True):
        response = self.post("/api/maintenance/corrective", {"asset_id": self.asset.pk, "title": title}, key=title)
        self.assertEqual(response.status_code, 201, response.content)
        pk = response.json()["id"]
        self.assertEqual(self.post(f"/api/work-orders/{pk}/submit", {"revision": 1}).status_code, 200)
        if assign_center:
            center = self.post("/api/budgeting/centers", {"site_id": self.site.pk, "code": "MAINT", "name": "Maintenance"}, key="center")
            self.assertIn(center.status_code, (200, 201), center.content)
            self.action(pk, "cost_center", cost_center_id=center.json()["id"])
        self.action(pk, "authorize")
        self.action(pk, "assign", assignee_id=self.technician.pk)
        self.action(pk, "start", client=self.tech)
        return pk

    def action(self, pk, action, client=None, expected=200, **values):
        response = self.post(f"/api/maintenance/orders/{pk}",
            {"action": action, "revision": WorkOrder.objects.get(pk=pk).revision, **values}, client)
        self.assertEqual(response.status_code, expected, response.content)
        return response

    def issue(self, pk, client=None, key="issue"):
        return self.post(f"/api/maintenance/orders/{pk}/parts",
                         {"item_id": self.item.pk, "location_id": self.location.pk, "quantity": "1"}, client, key)

    def test_asset_linked_general_order_is_distinct_from_cost_center_maintenance_order(self):
        self.activate()
        general = self.post("/api/work-orders", {"site_id": self.site.pk, "title": "General inspection",
                            "asset_reference": self.asset.pk}, key="general")
        self.assertEqual(general.status_code, 201, general.content)
        self.assertNotIn("maintenance_stage", general.json())
        self.assertEqual(self.client.get(f"/api/maintenance/orders/{general.json()['id']}").status_code, 404)
        pk = self.new_running_order("Repair pump", assign_center=False)
        job = self.client.get(f"/api/maintenance/orders/{pk}").json()
        self.assertTrue(job["center_required"])
        self.assertFalse(job["centers"])
        denied = self.issue(pk)
        self.assertEqual(denied.status_code, 409)
        self.assertEqual(denied.json()["error"], "cost_center_required")
        center = self.post("/api/budgeting/centers", {"site_id": self.site.pk, "code": "MECH", "name": "Mechanical"},
                           key="mechanical").json()
        self.action(pk, "cost_center", cost_center_id=center["id"], client=self.tech, expected=403)
        self.action(pk, "cost_center", cost_center_id=center["id"])
        self.assertEqual(self.issue(pk).status_code, 201)

    def test_plan_idempotency_date_anchor_and_database_constraint(self):
        plan = self.plan()
        first = self.generate(plan, "generate-one")
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(self.generate(plan, "generate-two").json(), {"id": first.json()["id"], "generated": False})
        state = self.client.get("/api/maintenance/plans").json()["items"][0]
        self.assertEqual(state["next_due"], "2026-02-28")
        response = self.post(f"/api/maintenance/plans/{plan['id']}/generate", {"due_date": "2026-02-28"}, key="next")
        self.assertEqual(response.json()["error"], "maintenance_previous_open")
        existing = MaintenanceJob.objects.get(plan_id=plan["id"])
        with self.assertRaises(IntegrityError), transaction.atomic():
            MaintenanceJob.objects.create(order=WorkOrder.objects.create(title="duplicate", site=self.site),
                asset=self.asset, plan_id=plan["id"], due_date=existing.due_date)
        self.assertEqual(MaintenancePlan.objects.count(), 1)

    def test_permissions_cost_and_financial_close(self):
        self.activate()
        pk = self.new_running_order("Corrective pump")
        self.assertEqual(self.tech.get("/api/inbox?kind=wo").json()["items"][0]["action"], "execute_maintenance")
        self.assertEqual(self.issue(pk, client=self.tech).status_code, 403)
        issue = self.issue(pk)
        self.assertEqual(issue.status_code, 201, issue.content)
        self.assertEqual(issue.json()["net"], "100.00")
        self.assertTrue(Posting.objects.get(movement_id=issue.json()["id"]).entry.lines.filter(
            account__kind="expense", cost_center__code="MAINT").exists())
        issue_posting = Posting.objects.get(movement_id=issue.json()["id"])
        self.assertEqual(dict(issue_posting.entry.lines.values_list("account__code", "amount")),
                         {"1200": Decimal("-100.00"), "MAINT-EXP": Decimal("100.00")})
        self.assertEqual(self.issue(pk).json(), issue.json())
        self.assertEqual(self.issue(pk, key="excess").status_code, 409)
        path = f"/api/maintenance/orders/{pk}/parts"
        returned = self.post(path, {"original_id": issue.json()["id"], "quantity": "0.250"}, key="return")
        self.assertEqual(returned.status_code, 201, returned.content)
        self.assertEqual(returned.json()["net"], "75.00")
        self.assertEqual(Position.objects.get(item=self.item, location=self.location).quantity, Decimal("0.25"))
        posting = Posting.objects.get(movement_id=returned.json()["id"])
        self.assertTrue(posting.entry.lines.filter(account__kind="expense", cost_center__code="MAINT").exists())
        self.assertEqual(dict(posting.entry.lines.values_list("account__code", "amount")),
                         {"1200": Decimal("25.00"), "MAINT-EXP": Decimal("-25.00")})
        self.action(pk, "complete", client=self.tech, findings="Seal replaced", results=[])
        self.assertEqual(self.tech.get("/api/inbox?kind=wo").json()["items"], [])
        self.assertEqual(self.client.get("/api/inbox?kind=wo").json()["items"][0]["action"], "close_maintenance")
        posting.state = "pending"
        posting.save(update_fields=["state"])
        self.assertEqual(self.action(pk, "close", expected=409).json()["error"], "maintenance_pending_postings")
        posting.state = "posted"
        posting.save(update_fields=["state"])
        self.action(pk, "close")
        self.assertEqual(self.client.get(f"/api/maintenance/orders/{pk}").json()["costs"],
                         {"issued": "100.00", "returned": "25.00", "net": "75.00"})

    def test_site_scope_and_retirement(self):
        outsider = get_user_model().objects.create_user(username="west_tech")
        role = Role.objects.create(name="West maintenance", permissions=[
            "maintenance.view", "maintenance.plan", "workorders.view", "assets.view",
        ])
        Assignment.objects.create(user=outsider, role=role, site=self.other)
        client = Client()
        client.force_login(outsider)
        plan = self.plan()
        self.assertEqual(client.get("/api/maintenance/plans").json()["items"], [])
        self.assertEqual(client.get(f"/api/maintenance/assets/{self.asset.pk}").status_code, 404)
        response = self.client.patch(f"/api/maintenance/assets/{self.asset.pk}",
                                     {"state": "retired", "revision": 0}, content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.generate(plan, "retired").json()["error"], "maintenance_asset_retired")

    def concurrent(self, call, entries):
        barrier = Barrier(2)

        def send(value):
            try:
                client = Client()
                self.login_admin(client)
                barrier.wait(10)
                return call(value, client)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(send, entries))

    @skipUnless(connection.vendor == "postgresql", "PostgreSQL row locking required")
    def test_postgres_concurrent_issue_and_plan_generation(self):
        self.activate()
        first = self.new_running_order("First order")
        second = self.new_running_order("Second order")
        result = self.concurrent(lambda pk, client: self.issue(pk, client=client, key=str(pk)), (first, second))
        self.assertEqual(sorted(r.status_code for r in result), [201, 409])
        self.assertEqual(Position.objects.get(item=self.item, location=self.location).quantity, 0)
        plan = self.plan()
        generated = self.concurrent(lambda key, client: self.generate(plan, key, client=client), ("one", "two"))
        self.assertEqual([row.status_code for row in generated], [201, 201])
        self.assertEqual(len({row.json()["id"] for row in generated}), 1)
        self.assertEqual(MaintenanceJob.objects.filter(plan_id=plan["id"]).count(), 1)
