from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from importlib import import_module
from threading import Barrier

from django.apps import apps
from django.db import connections
from django.test import Client, TransactionTestCase
from django.utils import timezone

from accounting.models import Posting
from backbone.models import ModuleState
from inventory.models import Item, Location, Movement, Position, Unit, Warehouse
from purchasing.test_orders import PurchaseOrderFixture


class SalesTests(PurchaseOrderFixture, TransactionTestCase):
    def setUp(self):
        super().setUp()
        for module in ("inventory", "accounting", "sales", "customers"):
            ModuleState.objects.update_or_create(module_id=module, defaults={"enabled": True})
        with connections["default"].schema_editor() as editor:
            import_module("accounting.migrations.0002_default_ledger").seed(apps, editor)
            import_module("accounting.migrations.0004_sales_rules").seed_sales(apps, editor)
        self.today = timezone.now().date()
        self.date = self.today.isoformat()
        self.period = self.post("/api/accounting/periods", {"start": self.today.replace(day=1).isoformat()}).json()["id"]
        path = "/api/accounting/cutover"
        token = self.post(path, {"cutoff": self.date}).json()["token"]
        self.assertEqual(self.post(path, {"cutoff": self.date, "token": token}).status_code, 201)
        unit, _ = Unit.objects.get_or_create(code="PC", defaults={"name": "Piece"})
        self.item = Item.objects.create(code="SEAL", name="Seal", base_unit=unit)
        self.location = Location.objects.create(code="A1", name="Shelf", warehouse=Warehouse.objects.create(
            site=self.east, code="WH", name="Main"))
        opened = self.post("/api/inventory/opening", {"site_id": self.east.pk, "lines": [
            {"item_id": self.item.pk, "location_id": self.location.pk, "quantity": "10", "unit_cost": "65"}]},
            HTTP_IDEMPOTENCY_KEY="stock")
        self.assertEqual(opened.status_code, 201, opened.content)
        self.customer = self.post("/api/sales/customers", {"code": "ACME", "name": "ACME", "terms": 30}).json()["id"]

    def make(self, kind, key, *, client=None, **values):
        return self.post("/api/sales/documents", {"kind": kind, "date": self.date, **values},
                         client, HTTP_IDEMPOTENCY_KEY=key)

    def action(self, row, action):
        result = self.patch(f"/api/sales/documents/{row['id']}", {"revision": row["revision"], "action": action})
        self.assertEqual(result.status_code, 200, result.content)
        return result.json()

    def order(self, key, qty="10"):
        quote = self.make("quote", key + "-q", site_id=self.east.pk, customer_id=self.customer,
            expires=(self.today + timedelta(days=10)).isoformat(),
            lines=[{"item_id": self.item.pk, "quantity": qty, "price": "100"}])
        self.assertEqual(quote.status_code, 201, quote.content)
        accepted = self.action(self.action(quote.json(), "send"), "accept")
        order = self.make("order", key + "-o", source_id=accepted["id"])
        self.assertEqual(order.status_code, 201, order.content)
        return self.action(order.json(), "confirm")

    def dispatch(self, row, qty, key, client=None):
        return self.make("delivery", key, client=client, source_id=row["id"],
            lines=[{"line_id": 1, "quantity": qty, "location_id": self.location.pk}])

    def child(self, kind, source, qty, key, **extra):
        response = self.make(kind, key, source_id=source["id"], lines=[{"line_id": 1, "quantity": qty}], **extra)
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def amounts(self, row):
        return dict(Posting.objects.get(sale__document_id=row["id"]).entry.lines.values_list("account__code", "amount"))

    def test_partial_sales_accounting_and_credit_return(self):
        order = self.order("a")
        shipped = self.dispatch(order, "6", "ship")
        self.assertEqual(shipped.status_code, 201, shipped.content)
        delivery = shipped.json()
        self.assertEqual(self.dispatch(order, "6", "ship").json(), delivery)
        self.assertEqual(self.amounts(delivery), {"SALES-UNB": 600, "SALES-REV": -600, "SALES-COGS": 390, "1200": -390})
        inbox = "/api/inbox?kind=sales&action=invoice"
        self.assertEqual(self.client.get(inbox).json()["items"][0]["remaining"], "6.000")
        invoice = self.child("invoice", delivery, "4", "invoice")
        self.assertEqual(self.client.get(inbox).json()["items"][0]["remaining"], "2.000")
        self.assertEqual(self.amounts(invoice), {"SALES-AR": 400, "SALES-UNB": -400})
        self.assertContains(self.client.get(f"/sales/print/{invoice['id']}"), "ACME")
        invalid = self.make("invoice", "duplicate", source_id=delivery["id"], lines=[{"line_id": 1, "quantity": "3"}])
        self.assertEqual(invalid.json()["error"], "sales_invoice_excess")
        paid = self.make("receipt", "receipt", source_id=invoice["id"], lines=[{"invoice_id": invoice["id"], "amount": "150"}])
        self.assertEqual(paid.status_code, 201, paid.content)
        self.assertEqual(self.amounts(paid.json()), {"1100": 150, "SALES-AR": -150})
        self.assertEqual(self.client.get("/api/sales/aging").json()["items"][0]["outstanding"], "250.00")
        credit = self.child("credit", invoice, "1", "credit", reason="Return accepted")
        self.assertEqual(self.amounts(credit), {"SALES-REV": 100, "SALES-AR": -100})
        self.assertEqual(Position.objects.get(item=self.item).quantity, 4)
        returned = self.child("return", credit, "1", "return", reason="Received")
        self.assertEqual(self.amounts(returned), {"1200": 65, "SALES-COGS": -65})
        unbilled = self.child("return", delivery, "1", "unbilled", reason="Received before invoice")
        self.assertEqual(self.amounts(unbilled), {"1200": 65, "SALES-COGS": -65, "SALES-REV": 100, "SALES-UNB": -100})
        self.assertEqual(Position.objects.get(item=self.item).quantity, 6)
        self.assertEqual(self.client.get("/api/sales/aging").json()["items"][0]["outstanding"], "150.00")
        self.assertEqual(self.outside.get(f"/api/sales/documents/{invoice['id']}").status_code, 404)

    def test_cost_center_on_cost_and_original_return(self):
        center = self.post("/api/budgeting/centers", {"site_id": self.east.pk, "code": "SALES", "name": "Sales"},
                           HTTP_IDEMPOTENCY_KEY="center").json()
        row = self.order("budget")
        path = f"/api/sales/documents/{row['id']}"
        assigned = self.patch(path, {"action": "cost_center", "revision": row["revision"], "cost_center_id": center["id"]})
        self.assertEqual(assigned.status_code, 200, assigned.content)
        shipment = self.dispatch(row, "2", "budget-shipment")
        self.assertEqual(shipment.status_code, 201, shipment.content)
        self.assertEqual(self.patch(path, {"action": "cost_center", "revision": assigned.json()["revision"],
                                          "cost_center_id": None}).status_code, 409)
        returned = self.child("return", shipment.json(), "1", "budget-return", reason="Returned")
        for doc, amount in ((shipment.json(), 130), (returned, -65)):
            posting = Posting.objects.get(sale__document_id=doc["id"])
            line = posting.entry.lines.get(account__code="SALES-COGS")
            self.assertEqual((line.cost_center_id, line.amount), (center["id"], amount))

    def test_concurrent_delivery_never_makes_stock_negative(self):
        orders = [self.order(key, "7") for key in ("one", "two")]
        barrier = Barrier(2)

        def deliver(index):
            try:
                client = Client()
                client.cookies = self.client.cookies.copy()
                barrier.wait(timeout=10)
                result = self.dispatch(orders[index], "7", f"shipment-{index}", client)
                return result.status_code, result.json()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(deliver, (0, 1)))
        self.assertEqual(sorted(status for status, _ in results), [201, 409])
        self.assertEqual(next(data["error"] for status, data in results if status == 409), "insufficient_stock")
        self.assertEqual(Position.objects.get(item=self.item).quantity, Decimal("3"))
        self.assertEqual(Movement.objects.filter(kind="sale").count(), 1)
        self.assertEqual(Posting.objects.filter(sale__document__kind="delivery", state="posted").count(), 1)

    def test_closed_period_and_dependent_posting_retry(self):
        self.assertEqual(self.post(f"/api/accounting/periods/{self.period}/close", {}).status_code, 200)
        shipped = self.dispatch(self.order("closed", "2"), "2", "closed-delivery").json()
        self.assertEqual(shipped["posting"]["error"], "finance_period_closed")
        billed = self.child("invoice", shipped, "2", "closed-invoice")
        self.assertEqual(billed["posting"]["error"], "finance_sales_source_pending")
        month = self.today.replace(day=28) + timedelta(days=4)
        later = month.replace(day=1).isoformat()
        self.assertEqual(self.post("/api/accounting/periods", {"start": later}).status_code, 201)
        for doc in (shipped, billed):
            posting = Posting.objects.get(sale__document_id=doc["id"])
            result = self.post(f"/api/accounting/postings/{posting.pk}/retry", {"date": later, "reason": "Late"})
            self.assertEqual(result.json()["state"], "posted", result.content)
            self.assertEqual(result.json()["sale_id"], doc["id"])
            self.assertEqual(self.post(f"/api/accounting/postings/{posting.pk}/retry", {}).json()["entry_id"],
                             result.json()["entry_id"])
