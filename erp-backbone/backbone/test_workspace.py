from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.utils import timezone

from accounting.models import Entry, Ledger, Posting
from sales.models import Customer, Document, PostingEvent
from workorders.models import WorkOrder
from .access import audit
from .models import Assignment, AuditEvent, ExperienceNotice, ModuleState, Role, Site


class WorkspaceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("operator")
        self.site = Site.objects.create(name="East")
        self.other = Site.objects.create(name="West")
        self.client.force_login(self.user)

    def permit(self, *permissions, site=None):
        Assignment.objects.create(user=self.user, site=site, role=Role.objects.create(
            name=" / ".join(permissions), permissions=permissions))

    def write(self, path, values, method="patch"):
        return getattr(self.client, method)(path, values, content_type="application/json")

    def test_profile_keeps_login_and_audit_actor_identity(self):
        before = self.user
        audit(before, "before", "test")
        response = self.write("/api/profile", {"first_name": "Ahmed", "middle_name": "M.", "last_name": "Salama"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["display_name"], "Ahmed M. Salama")
        self.assertEqual(self.client.get("/api/workspace").json()["username"], "operator")
        self.assertEqual(self.client.get("/api/workspace").json()["display_name"], "Ahmed M. Salama")
        self.assertEqual(AuditEvent.objects.get(action="before").actor_user_id, before.pk)
        self.assertEqual(AuditEvent.objects.get(action="before").actor, "operator")
        self.assertEqual(self.write("/api/profile", {"employee_number": "001"}).status_code, 400)
        self.assertEqual(self.write("/api/preferences/module-view", {"module_view": "grid"}).status_code, 403)
        self.assertEqual(self.client.get("/profile").status_code, 200)

    def test_counters_match_lists_without_notice_queries_per_record(self):
        ModuleState.objects.create(module_id="workorders", enabled=True)
        self.permit("workorders.view", site=self.site)
        self.client.get("/api/workspace/counters")
        with CaptureQueriesContext(connection) as baseline:
            self.assertEqual(self.client.get("/api/workspace/counters").json(), {"inbox": 0, "unread": 0})
        for i in range(20):
            row = WorkOrder.objects.create(title=f"Test WO {i}", site=self.site)
            ExperienceNotice.objects.create(user=self.user, key=f"test:{i}", kind="wo",
                                            object_id=row.pk, event="approved")
        hidden = WorkOrder.objects.create(title="Out of scope", site=self.other)
        ExperienceNotice.objects.create(user=self.user, key="hidden", kind="wo", object_id=hidden.pk, event="approved")
        with CaptureQueriesContext(connection) as loaded:
            counts = self.client.get("/api/workspace/counters").json()
        self.assertLessEqual(len(loaded), len(baseline) + 1)
        self.assertEqual(counts["inbox"], len(self.client.get("/api/inbox").json()["items"]))
        self.assertEqual(counts["unread"], self.client.get("/api/notifications").json()["unread"])
        self.assertEqual(counts["unread"], 20)
        self.assertEqual(self.write("/api/notifications", {"key": "test:0"}, "post").status_code, 200)
        self.assertEqual(self.client.get("/api/workspace/counters").json()["unread"], 19)

    def test_dashboard_and_drilldown_agree_on_posting_date_scope_and_currency(self):
        ModuleState.objects.create(module_id="sales", enabled=True)
        self.permit("sales.view", site=self.site)
        customer = Customer.objects.create(code="T", name="Customer")
        today = timezone.localdate()
        ledger = Ledger.objects.get(pk=1)
        other_currency = Ledger.objects.create(pk=ledger.pk + 1, currency="USD")
        for kind, amount, site, state, currency in (
                ("invoice", "100", self.site, "posted", ledger),
                ("credit", "25", self.site, "posted", ledger),
                ("invoice", "10", self.site, "posted", other_currency),
                ("invoice", "900", self.other, "posted", ledger),
                ("invoice", "55", self.site, "pending", ledger)):
            doc = Document.objects.create(kind=kind, amount=Decimal(amount), site=site, customer=customer,
                                          status="recorded", actor="operator", date=today - timedelta(days=40))
            post = PostingEvent.objects.create(document=doc)
            entry = Entry.objects.create(ledger=currency, date=today, kind=kind, created_by="operator") if state == "posted" else None
            Posting.objects.create(ledger=currency, sale=post, state=state, entry=entry)
        card = self.client.get("/api/workspace/dashboard", {"site": self.site.pk}).json()
        self.assertEqual(card["sales"], {"AED": "75.00", "USD": "10.00"})
        self.assertNotIn("ready_purchase_orders", card)
        detail = self.client.get("/api/reports/advanced/net-posted-sales", {**card["period"], "site": self.site.pk}).json()
        self.assertEqual(detail["currencies"], card["sales"])
        self.assertEqual(detail["count"], 3)
        self.assertEqual(self.client.get("/api/workspace/dashboard", {"site": self.other.pk}).json()["sales"], {})
        path = "/api/workspace/dashboard/charts"
        filters = {"site": self.site.pk, "from": (today - timedelta(days=5)).isoformat(),
                   "to": today.isoformat(), "group": "week"}
        with CaptureQueriesContext(connection) as log:
            response = self.client.get(path, filters)
        self.assertEqual(response.status_code, 200, response.content)
        sales_queries = [query["sql"] for query in log if 'FROM "sales_document"' in query["sql"]]
        series = response.json()["widgets"]["sales"]
        self.assertEqual({row["currency"] for row in series}, {"AED", "USD"})
        for row in series:
            point = row["points"][-1]
            detail = self.client.get("/api/reports/advanced/net-posted-sales", point["filters"])
            self.assertEqual(detail.json()["currencies"][row["currency"]], point["value"])
            self.assertEqual(point["to"], today.isoformat())
        self.assertEqual(len(sales_queries), 2)
        self.assertTrue(all("GROUP BY" in query for query in sales_queries))
        self.assertNotIn("sales", self.client.get(path, {"site": self.other.pk}).json()["widgets"])
        self.assertEqual(self.client.get(path, {"group": "year"}).status_code, 400)
        ModuleState.objects.filter(pk="sales").update(enabled=False)
        self.assertNotIn("sales", self.client.get(path).json()["widgets"])


    def test_ready_orders_use_issue_scope_and_match_dashboard_and_inbox(self):
        from purchasing.models import PurchaseOrder, Requisition, Supplier

        for module in ("purchasing", "purchase_orders", "suppliers"):
            ModuleState.objects.update_or_create(module_id=module, defaults={"enabled": True})
        self.permit("purchasing.view")
        self.permit("purchasing.po.issue", site=self.site)
        supplier = Supplier.objects.create(code="T", name="Supplier", currency_default="AED")

        def order(number, site, status):
            source = Requisition.objects.create(title=number, site=site, currency="AED", total=10, lines=[])
            return PurchaseOrder.objects.create(number=number, source_pr=source, supplier=supplier,
                title=number, site=site, currency="AED", total=10, status=status, lines=[])

        ready = order("PO-1", self.site, "draft")
        order("PO-2", self.site, "issued")
        order("PO-3", self.other, "draft")
        self.assertEqual(self.client.get("/api/workspace/counters").json()["inbox"], 1)
        self.assertEqual(self.client.get("/api/workspace/dashboard").json()["ready_purchase_orders"], 1)
        found = self.client.get("/api/purchase-orders", {"ready": "1", "site": self.site.pk}).json()["items"]
        self.assertEqual([row["id"] for row in found], [ready.pk])
        self.assertEqual(self.client.get("/api/purchase-orders", {"ready": "1", "site": self.other.pk}).json()["items"], [])
        self.assertEqual(len(self.client.get("/api/purchase-orders").json()["items"]), 3)

    def test_profile_employee_number_is_scoped(self):
        from hr.models import Employee

        ModuleState.objects.create(module_id="hr", enabled=True)
        Employee.objects.create(user=self.user, number="E1", name="Employee", site=self.site,
                                department="Ops", title="Operator", joined=timezone.localdate())
        self.assertIsNone(self.client.get("/api/profile").json()["employee_number"])
        self.permit("hr.self", site=self.site)
        self.assertEqual(self.client.get("/api/profile").json()["employee_number"], "E1")
