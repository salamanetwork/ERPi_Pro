from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from accounting.models import Account, Entry, Ledger, Line
from assets.models import Asset, AssetTransfer
from inventory.models import Item, Location, Movement, Receipt, Unit, Warehouse
from purchasing.models import PurchaseOrder, Requisition, Supplier
from sales.models import Customer, Document
from workorders.models import MaintenanceJob, WorkOrder

from .models import Assignment, AuditEvent, ModuleState, NumberingCounter, NumberingRule, Role
from .tests import BackboneTests


class MasterAdminTests(BackboneTests):
    def update_site(self, *, active):
        return self.client.patch(f"/api/sites/{self.site_a.pk}",
                                 {"name": self.site_a.name, "active": active, "revision": 1},
                                 content_type="application/json")

    def test_site_blockers_and_read_only_history(self):
        Assignment.objects.create(user=self.operator, site=self.site_a, role=Role.objects.create(name="Site operator"))
        warehouse = Warehouse.objects.create(site=self.site_a, code="WH", name="Warehouse")
        pr = Requisition.objects.create(site=self.site_a, title="PR", total=1, currency="AED", lines=[])
        supplier = Supplier.objects.create(code="T1", name="Supplier", currency_default="AED")
        po = PurchaseOrder.objects.create(site=self.site_a, source_pr=pr, supplier=supplier,
                                          title="PO", number="P1", total=1, currency="AED", lines=[])
        Receipt.objects.create(site=self.site_a, order=po, warehouse=warehouse, number="GR1", lines=[])
        WorkOrder.objects.create(site=self.site_a, title="WO")
        Document.objects.create(site=self.site_a, customer=Customer.objects.create(code="C1", name="Customer"),
                                kind="order", status="draft", date=timezone.localdate())
        response = self.update_site(active=False)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["blockers"], {key: 1 for key in (
            "assignments", "warehouses", "purchase_requests", "purchase_orders",
            "goods_receipts", "work_orders", "sales_orders")})
        self.site_a.refresh_from_db()
        self.assertTrue(self.site_a.active)
        response = self.client.patch(f"/api/sites/{self.site_b.pk}",
                                     {"name": "Renamed", "active": False, "revision": 1},
                                     content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.toggle("workorders")
        self.assertEqual(self.create_order(site_id=self.site_b.pk).status_code, 409)
        self.assertIn({"id": self.site_b.pk, "name": "Renamed", "active": False},
                      self.client.get("/api/workspace").json()["sites"])

    def test_transfer_keeps_historical_expense_site(self):
        self.toggle("assets")
        self.toggle("workorders")
        asset = Asset.objects.create(site=self.site_a, name="Pump", tag="T")
        order = WorkOrder.objects.create(site=self.site_a, title="Service", status="approved")
        job = MaintenanceJob.objects.create(asset=asset, order=order)
        day = timezone.localdate()
        warehouse = Warehouse.objects.create(site=self.site_a, code="WH", name="Warehouse")
        location = Location.objects.create(warehouse=warehouse, code="L", name="Shelf")
        unit, _ = Unit.objects.get_or_create(code="PC", defaults={"name": "Piece"})
        move = Movement.objects.create(site=self.site_a, item=Item.objects.create(code="P", name="Part", base_unit=unit),
            location=location, kind="maint_issue", quantity=-1, value=Decimal("-25.00"),
            actor="admin", actor_id=self.admin.pk, occurred_at=timezone.now(), business_date=day,
            facts={"work_order_id": order.pk, "asset_id": asset.pk})
        ledger = Ledger.objects.get(pk=1)
        entry = Entry.objects.create(ledger=ledger, date=day, kind="test", created_by="admin",
                                     posted_at=timezone.now())
        account = Account.objects.get(ledger=ledger, code="1100")
        expense = Line.objects.create(entry=entry, account=account, site=self.site_a, amount=Decimal("25.00"))
        payload = {"revision": 1, "destination_site_id": self.site_b.pk, "transferred_on": day.isoformat(), "reason": "Relocate"}
        url = f"/api/assets/{asset.pk}/transfer"
        self.assertEqual(self.client.post(url, payload, content_type="application/json", HTTP_IDEMPOTENCY_KEY="move").status_code, 409)
        job.stage = "closed"
        job.save(update_fields=["stage"])
        result = self.client.post(url, payload, content_type="application/json", HTTP_IDEMPOTENCY_KEY="move")
        self.assertEqual(result.status_code, 201, result.content)
        self.assertEqual(self.client.post(url, payload, content_type="application/json", HTTP_IDEMPOTENCY_KEY="move").status_code, 200)
        self.assertEqual(AssetTransfer.objects.get(asset=asset).source_site_id, self.site_a.pk)
        self.assertTrue(AuditEvent.objects.filter(actor_user_id=self.admin.pk, action="asset.transfer").exists())
        for row in [order, move, asset]:
            row.refresh_from_db()
        self.assertEqual(asset.site_id, self.site_b.pk)
        self.assertEqual(order.site_id, self.site_a.pk)
        self.assertEqual(move.site_id, self.site_a.pk)
        expense.refresh_from_db()
        self.assertEqual(expense.site_id, self.site_a.pk)
        self.assertEqual(expense.amount, Decimal("25.00"))
        filters = {"from": (day - timedelta(days=1)).isoformat(), "to": day.isoformat()}
        path = "/api/reports/advanced/maintenance-parts"
        source = self.client.get(path, {**filters, "site": self.site_a.pk}).json()
        destination = self.client.get(path, {**filters, "site": self.site_b.pk}).json()
        self.assertEqual(source["amount"], "25.00")
        self.assertEqual(destination["amount"], "0.00")

    def test_preview_is_read_only_before_and_after_allocation(self):
        from .services.numbering import allocate_number

        payload = {"prefix": "PO", "start": 1, "per_site": False, "revision": 1}
        url = "/api/numbering-rules/po/preview"
        self.assertEqual(self.client.post(url, payload, content_type="application/json").json()["number"], "PO-000001")
        self.assertFalse(NumberingRule.objects.filter(kind="po").exists())
        self.assertEqual(allocate_number("po", self.site_a.pk), "PO-000001")
        for _ in range(2):
            self.assertEqual(self.client.post(url, payload, content_type="application/json").json()["number"], "PO-000002")
        self.assertEqual(NumberingCounter.objects.get(rule__kind="po").last, 1)
        update = self.client.patch("/api/numbering-rules/po", {**payload, "prefix": "NEW", "apply_to_future": True},
                                   content_type="application/json")
        self.assertEqual(update.status_code, 200)
        self.assertEqual(allocate_number("po", self.site_a.pk), "NEW-000002")
        rendered = self.client.post("/api/print-templates/po/ar/preview", {"heading": "Test <script>"},
                                    content_type="application/json")
        self.assertContains(rendered, 'dir="rtl"')
        self.assertContains(rendered, "Test &lt;script&gt;")

    def test_audit_export_and_navigation_are_separate_from_access(self):
        self.toggle("assets")
        AuditEvent.objects.create(actor="Legacy", action="specific.test", target="=1+1")
        exported = self.client.get("/api/audit/export", {"action": "specific.test"})
        self.assertEqual(exported.status_code, 200)
        csv = b"".join(exported.streaming_content).decode("utf-8-sig")
        self.assertIn("'=1+1", csv)
        self.assertNotIn("module.enable", csv)
        self.assertTrue(AuditEvent.objects.filter(action="audit.export").exists())
        role = Role.objects.create(name="Asset viewer", permissions=["assets.view"], hidden_modules=["assets"])
        Assignment.objects.create(user=self.operator, role=role, site=self.site_a)
        self.client.force_login(self.operator)
        module = lambda: next(row for row in self.client.get("/api/workspace").json()["modules"] if row["id"] == "assets")
        self.assertTrue(module()["visible"])
        self.assertFalse(module()["show_in_navigation"])
        self.assertEqual(self.client.get("/api/assets").status_code, 200)
        self.assertEqual(self.client.get("/api/audit/export").status_code, 403)
        Assignment.objects.create(user=self.operator, role=Role.objects.create(name="Second role"), site=self.site_a)
        self.assertTrue(module()["show_in_navigation"])

    def test_supplier_archive_preserves_purchase_order_snapshot(self):
        self.toggle("purchasing")
        self.toggle("suppliers")
        self.toggle("purchase_orders")
        payload = {"code": "VEN", "name": "Vendor", "currency_default": "AED", "active": True}
        result = self.client.post("/api/suppliers", payload, content_type="application/json")
        self.assertEqual(result.status_code, 201)
        supplier = result.json()
        source = Requisition.objects.create(site=self.site_a, title="PR", total=10, currency="AED", lines=[])
        order = PurchaseOrder.objects.create(site=self.site_a, source_pr=source, supplier_id=supplier["id"],
            title="PO", number="POX", total=10, currency="AED", status="issued", lines=[],
            snapshot={"supplier": {"name": "Vendor at issue"}, "issued_by": "admin"})
        archived = self.client.patch("/api/suppliers/" + str(supplier["id"]),
            {**payload, "name": "Archived name", "active": False, "revision": supplier["revision"]},
            content_type="application/json")
        self.assertEqual(archived.status_code, 200)
        self.assertEqual(self.client.get(f"/api/purchase-orders/{order.pk}").json()["supplier_name"], "Vendor at issue")
        role = Role.objects.create(name="Supplier reader", permissions=["suppliers.view", "purchasing.view"])
        Assignment.objects.create(user=self.operator, role=role)
        self.client.force_login(self.operator)
        self.assertFalse(self.client.get("/api/suppliers?directory=1").json()["items"][0]["active"])
        self.assertEqual(self.client.get("/api/suppliers").json()["items"], [])
        self.assertEqual(self.client.post("/api/suppliers", payload, content_type="application/json").status_code, 403)
