from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from importlib import import_module
from threading import Barrier
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connections, transaction
from django.test import Client, TransactionTestCase

from backbone.models import Assignment, ModuleState, Role
from inventory.models import Item, Location, Unit, Warehouse
from purchasing.test_orders import PurchaseOrderFixture
from .models import Accrual, Entry, SupplierInvoice


class PayablesTests(PurchaseOrderFixture, TransactionTestCase):
    def setUp(self):
        super().setUp()
        for name in ("inventory", "accounting", "supplier_invoices"):
            ModuleState.objects.update_or_create(module_id=name, defaults={"enabled": True})
        with connections["default"].schema_editor() as editor:
            import_module("accounting.migrations.0002_default_ledger").seed(apps, editor)
            import_module("accounting.migrations.0006_payable_accounts").seed(apps, editor)
        for day in ("2026-11-01", "2026-12-01"):
            response = self.post("/api/accounting/periods", {"start": day})
            self.assertEqual(response.status_code, 201, response.content)
        draft = self.convert().json()
        self.po = self.post(f"/api/purchase-orders/{draft['id']}/issue", {"revision": draft["revision"]}).json()
        self.warehouse = Warehouse.objects.create(site=self.east, code="WH", name="Main")
        self.location = Location.objects.create(warehouse=self.warehouse, code="01", name="Shelf")
        self.unit, _ = Unit.objects.get_or_create(code="PC", defaults={"name": "Piece"})
        self.item = Item.objects.create(code="SEAL", name="Seal", base_unit=self.unit)
        self.actor = get_user_model().objects.create_user(username="ap_poster")
        role = Role.objects.create(name="AP poster", permissions=["ap.view", "ap.post", "ap.pay", "ap.credit"])
        Assignment.objects.create(user=self.actor, role=role, site=self.east)
        self.poster = Client()
        self.poster.force_login(self.actor)

    def activate(self):
        path = "/api/accounting/cutover"
        payload = {"cutoff": "2026-11-01"}
        preview = self.post(path, payload).json()
        self.assertEqual(self.post(path, {**payload, "token": preview["token"]}).status_code, 201)

    def receipt(self, quantity, day, key):
        response = self.post("/api/inventory/receipts", {"order_id": self.po["id"], "warehouse_id": self.warehouse.pk,
            "lines": [{"order_line_id": 1, "quantity": quantity, "item_id": self.item.pk, "unit_id": self.unit.pk,
                       "location_id": self.location.pk}]}, HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual(response.status_code, 201, response.content)
        row = response.json()
        with patch("inventory.stock.business_date", return_value=date.fromisoformat(day)):
            response = self.post(f"/api/inventory/receipts/{row['id']}/confirm", {"revision": row["revision"]},
                                 HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["lines"][0]["movement_id"]

    def draft(self, number, lines, total, key=None):
        return self.post("/api/ap/invoices", {"order_id": self.po["id"], "supplier_number": number,
            "invoice_date": "2026-11-05", "due_date": "2026-11-10", "declared_total": total,
            "lines": [{"movement_id": move, "quantity": qty, "unit_price": price} for move, qty, price in lines]},
            HTTP_IDEMPOTENCY_KEY=key or number)

    def finalize(self, invoice, client=None):
        return self.post(f"/api/ap/invoices/{invoice['id']}/post", {"revision": invoice["revision"],
            "posting_date": "2026-11-05"}, client or self.poster, HTTP_IDEMPOTENCY_KEY=invoice["number"])

    def aging(self, day):
        return self.poster.get("/api/ap/aging", {"as_of": day}).json()

    def test_finance_role_needs_explicit_ap_permissions_for_existing_draft(self):
        self.activate()
        move = self.receipt("3", "2026-11-02", "routing-receipt")
        draft = self.draft("ROUTING", [(move, "3", "33.3333")], "100.00").json()
        user = get_user_model().objects.create_user(username="finance_test")
        finance = Client()
        finance.force_login(user)
        role = Role.objects.create(name="Accounting_ROLES", permissions=["accounting.view", "accounting.post"])
        Assignment.objects.create(user=user, role=role)
        path = f"/api/ap/invoices/{draft['id']}"
        inbox = "/api/inbox?kind=si"
        self.assertTrue(self.post(path + "/match", {"posting_date": "2026-11-05", "revision": 1}).json()["matched"])
        self.assertEqual(finance.get(inbox).json()["items"], [])
        self.assertEqual(self.finalize(draft, finance).status_code, 404)
        routing = self.client.get(path).json()["posting_routing"]
        self.assertEqual(routing, {"site_id": self.east.pk, "created_by": "admin", "eligible_reviewers": ["ap_poster"]})

        ap_role = Role.objects.create(name="Supplier invoice poster", permissions=["ap.view", "ap.post"])
        grant = Assignment.objects.create(user=user, role=ap_role, site=self.west)
        self.assertEqual(finance.get(inbox).json()["items"], [])
        grant.site = self.east
        grant.save(update_fields=["site"])
        self.assertEqual([row["number"] for row in finance.get(inbox).json()["items"]], [draft["number"]])
        info = finance.get(path).json()
        self.assertNotIn("posting_routing", info)
        self.assertTrue(info["can_post"])
        self.assertIn("finance_test", self.client.get(path).json()["posting_routing"]["eligible_reviewers"])
        response = self.finalize(draft, finance)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(finance.get(inbox).json()["items"], [])

    def test_cutover_partial_invoicing_return_payment_and_historical_aging(self):
        first = self.receipt("1", "2026-10-31", "opening")
        self.activate()
        second = self.receipt("2", "2026-11-02", "regular")
        lines = [(first, "1", "33.3333"), (second, "1", "33.3333")]
        draft = self.draft("INV-01", lines, "66.67").json()
        self.assertEqual(self.draft(" inv-01 ", lines, "66.67", "duplicate").json()["error"], "ap_duplicate_invoice")
        with self.assertRaises(IntegrityError), transaction.atomic():
            duplicate = SupplierInvoice.objects.get(pk=draft["id"])
            duplicate.pk = None
            duplicate.save(force_insert=True)
        self.assertEqual(self.finalize(draft, self.client).status_code, 403)
        self.assertEqual(len(self.poster.get("/api/inbox?kind=si").json()["items"]), 1)
        posted = self.finalize(draft)
        self.assertEqual(posted.status_code, 201, posted.content)
        self.assertEqual(self.finalize(draft).json(), posted.json())
        self.assertEqual(self.poster.get("/api/inbox?kind=si").json()["items"], [])
        self.assertEqual(self.aging("2026-11-04")["items"], [])
        self.assertEqual(self.aging("2026-11-10")["total"], "66.67")
        with patch("inventory.views.business_date", return_value=date(2026, 11, 6)), patch(
                "inventory.stock.business_date", return_value=date(2026, 11, 6)):
            returned = self.post(f"/api/inventory/movements/{second}/reverse", {"kind": "return", "quantity": "2",
                "reason": "Return remaining and invoiced stock"}, HTTP_IDEMPOTENCY_KEY="return")
        self.assertEqual(returned.status_code, 201, returned.content)
        row = Accrual.objects.get(receipt_movement_id=second)
        self.assertEqual(row.remaining, 0)
        payment = self.post("/api/ap/payments", {"posting_date": "2026-11-12", "reference": "BANK-1",
            "allocations": [{"invoice_id": draft["id"], "amount": "15.00"}]}, self.poster, HTTP_IDEMPOTENCY_KEY="payment")
        self.assertEqual(payment.status_code, 201, payment.content)
        credit = self.post(f"/api/ap/invoices/{draft['id']}/credits", {"posting_date": "2026-11-20", "reference": "CN-1",
            "movement_id": returned.json()["id"], "reason": "Supplier confirmed", "amount": "33.34"},
            self.poster, HTTP_IDEMPOTENCY_KEY="credit")
        self.assertEqual(credit.status_code, 201, credit.content)
        self.assertEqual(self.aging("2026-11-10")["total"], "66.67")
        self.assertEqual(self.aging("2026-11-15")["total"], "51.67")
        self.assertEqual(self.aging("2026-11-20")["total"], "18.33")
        trace = self.client.get("/api/accounting/reports/grni").json()["items"]
        self.assertEqual(next(row for row in trace if row["movement_id"] == second)["returned_value"], "33.33")
        accounts = {row["code"]: Decimal(row["closing"]) for row in self.client.get(
            "/api/accounting/reports/trial?start=2026-11-01&end=2026-11-20").json()["items"]}
        self.assertEqual(accounts["2100"], 0)
        self.assertEqual(accounts["AP-RETURNS"], 0)
        self.assertEqual(accounts["AP-CONTROL"], Decimal("-18.33"))
        self.assertEqual(sum(accounts.values()), 0)

    def test_two_posts_cannot_consume_same_receipt(self):
        self.activate()
        move = self.receipt("3", "2026-11-02", "race-receipt")
        mismatch = self.draft("WRONG", [(move, "3", "33.3334")], "100.00").json()
        self.assertEqual(self.finalize(mismatch).json()["error"], "ap_price_mismatch")
        invoices = [self.draft(f"RACE-{key}", [(move, "3", "33.3333")], "100.00").json() for key in (1, 2)]
        barrier = Barrier(2)

        def send(row):
            try:
                client = Client()
                client.force_login(get_user_model().objects.get(pk=self.actor.pk))
                barrier.wait(10)
                response = self.finalize(row, client)
                return response.status_code, response.json()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(send, invoices))
        self.assertEqual(sorted(status for status, _ in results), [201, 409])
        self.assertEqual(next(data["error"] for status, data in results if status == 409), "ap_quantity_conflict")
        self.assertEqual(Entry.objects.filter(kind="ap_invoice").count(), 1)
        self.assertEqual(Accrual.objects.get(receipt_movement_id=move).remaining, 0)

    def test_invoice_reversal_restores_quantity_without_rewriting_aging(self):
        self.activate()
        move = self.receipt("3", "2026-11-02", "reverse-receipt")
        row = self.finalize(self.draft("REVERSE", [(move, "3", "33.3333")], "100.00").json()).json()
        response = self.post(f"/api/ap/invoices/{row['id']}/reverse", {"revision": row["revision"],
            "posting_date": "2026-11-20", "reason": "Wrong supplier document"}, self.poster, HTTP_IDEMPOTENCY_KEY="reverse")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(Accrual.objects.get(receipt_movement_id=move).remaining, 100)
        self.assertEqual(self.aging("2026-11-19")["total"], "100.00")
        self.assertEqual(self.aging("2026-11-20")["total"], "0.00")
