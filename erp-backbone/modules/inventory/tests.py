from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

from django.db import connections
from django.test import Client, TransactionTestCase

from backbone.models import ModuleState
from purchasing.models import PurchaseReceiptLink
from purchasing.test_orders import PurchaseOrderFixture

from .models import Movement, OrderLine


class InventoryTests(PurchaseOrderFixture, TransactionTestCase):
    def setUp(self):
        super().setUp()
        ModuleState.objects.create(module_id="inventory", enabled=True)
        self.unit = self.config("unit", code="TPC", name="Test Piece")
        self.box = self.config("unit", code="TBOX", name="Test Box")
        self.item = self.config("item", code="SEAL", name="Seal", base_unit_id=self.unit)
        self.warehouse = self.config("warehouse", code="WH", name="Main", site_id=self.east.pk)
        self.location = self.config("location", code="A1", name="Shelf A1", warehouse_id=self.warehouse)
        self.config("conversion", item_id=self.item, unit_id=self.box, factor="10")
        draft = self.convert().json()
        self.po = self.post(f"/api/purchase-orders/{draft['id']}/issue", {"revision": draft["revision"]}).json()

    def config(self, kind, **values):
        response = self.post(f"/api/inventory/setup/{kind}", values)
        self.assertIn(response.status_code, (200, 201), response.content)
        return response.json()["id"]

    def receipt(self, quantity, key, *, expected=201):
        data = {"order_id": self.po["id"], "warehouse_id": self.warehouse, "supplier_reference": "D-1",
                "lines": [{"order_line_id": 1, "item_id": self.item, "unit_id": self.box,
                           "location_id": self.location, "quantity": quantity}]}
        response = self.post("/api/inventory/receipts", data, HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual(response.status_code, expected, response.content)
        return response.json()

    def confirm(self, row, key=None, client=None):
        return self.post(f"/api/inventory/receipts/{row['id']}/confirm", {"revision": row["revision"]}, client,
                         HTTP_IDEMPOTENCY_KEY=key or row["number"])

    def stock(self):
        return self.client.get("/api/inventory/balances").json()["items"]

    def reverse(self, row, quantity, kind="return"):
        return self.post(f"/api/inventory/movements/{row['lines'][0]['movement_id']}/reverse",
                         {"quantity": quantity, "kind": kind, "reason": "Supplier return"},
                         HTTP_IDEMPOTENCY_KEY=f"{kind}-{quantity}")

    def opening(self, key, *, close=False):
        return self.post("/api/inventory/opening", {"site_id": self.east.pk, "close": close,
            "lines": [{"item_id": self.item, "location_id": self.location, "quantity": "10", "unit_cost": "5"}]},
            HTTP_IDEMPOTENCY_KEY=key)

    def test_opening_partial_full_return_and_receipt_print(self):
        initial = self.opening("opening")
        self.assertEqual(initial.status_code, 201, initial.content)
        self.assertEqual(self.opening("opening").json(), initial.json())
        self.assertEqual(self.opening("duplicate").json()["error"], "inventory_opening_duplicate")
        first = self.receipt("1", "first")
        self.assertEqual(self.client.get("/api/inbox?kind=grn").json()["items"][0]["number"], first["number"])
        self.assertEqual(self.stock()[0]["quantity"], "10.000000000")
        result = self.confirm(first)
        self.assertEqual(result.status_code, 201, result.content)
        self.assertEqual(self.client.get("/api/inbox?kind=grn").json()["items"], [])
        posted = result.json()
        self.assertEqual(self.confirm(first).json(), posted)
        self.assertEqual(posted["lines"][0]["amount"], "33.33")
        self.assertEqual(self.stock()[0]["site_average"], "4.166500000")
        self.assertEqual(self.opening("closed").json()["error"], "inventory_opening_closed")
        self.config("conversion", item_id=self.item, unit_id=self.box, factor="12")
        path = f"/api/inventory/orders/{self.po['id']}"
        self.assertEqual(self.client.get(path).json()["receipt_status"], "partially_received")
        excess = self.receipt("2.001", "excess")
        self.assertEqual(self.confirm(excess).json()["error"], "inventory_over_receipt")
        self.assertFalse(PurchaseReceiptLink.objects.filter(receipt_reference=f"grn:{excess['id']}").exists())
        second = self.receipt("2", "second")
        result = self.confirm(second)
        self.assertEqual(result.status_code, 201, result.content)
        second = result.json()
        self.assertEqual(second["lines"][0]["amount"], "66.67")
        self.assertEqual(self.stock()[0]["quantity"], "40.000000000")
        self.assertEqual(self.stock()[0]["site_average"], "3.750000000")
        self.assertEqual(self.client.get(path).json()["receipt_status"], "received")
        printed = self.client.get(f"/print/grn/{second['id']}?language=ar")
        self.assertContains(printed, 'dir="rtl"')
        self.assertContains(printed, "66.67")
        returned = self.reverse(posted, "0.5")
        self.assertEqual(returned.status_code, 201, returned.content)
        self.assertEqual(Decimal(returned.json()["value"]), Decimal("-18.75"))
        self.assertEqual(returned.json()["facts"]["original_receipt_value"], "16.67")
        self.assertEqual(self.client.get(path).json()["lines"][0]["remaining"], "0.500")
        self.assertEqual(self.reverse(posted, "0.501").json()["error"], "inventory_return_exceeds_receipt")
        self.assertEqual(self.post(f"/api/purchase-orders/{self.po['id']}/void",
                         {"revision": self.po["revision"], "reason": "void"}).json()["error"], "purchase_order_has_receipts")

    def test_correction_and_site_permissions(self):
        self.assertEqual(self.outside.get(f"/api/inventory/orders/{self.po['id']}").status_code, 404)
        draft = self.receipt("1", "one")
        self.assertEqual(self.buyer.get(f"/api/inventory/receipts/{draft['id']}").status_code, 404)
        confirmed = self.confirm(draft).json()
        correction = self.reverse(confirmed, "1", "correction")
        self.assertEqual(correction.status_code, 201, correction.content)
        self.assertEqual(Decimal(correction.json()["value"]), Decimal("-33.33"))
        self.assertEqual(self.stock(), [])
        self.assertEqual(Movement.objects.filter(original_id=confirmed["lines"][0]["movement_id"]).count(), 1)
        self.assertEqual(OrderLine.objects.get(order_id=self.po["id"]).received, 0)

    def test_concurrent_confirmations_reject_excess(self):
        drafts = [self.receipt("2", f"race-{n}") for n in range(2)]
        barrier = Barrier(2)

        def run(row):
            try:
                client = Client()
                client.cookies = self.client.cookies.copy()
                barrier.wait(timeout=10)
                return self.confirm(row, client=client).status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(run, drafts)), [201, 409])
        self.assertEqual(Movement.objects.filter(kind="receipt").count(), 1)
        self.assertEqual(self.stock()[0]["quantity"], "20.000000000")

    def test_confirm_versus_void_and_disabled_currency(self):
        from purchasing.models import PurchaseOrder

        PurchaseOrder.objects.filter(pk=self.po["id"]).update(currency="USD")
        self.assertEqual(self.receipt("1", "wrong-currency", expected=409)["error"], "inventory_aed_required")
        PurchaseOrder.objects.filter(pk=self.po["id"]).update(currency="AED")
        draft = self.receipt("1", "void-race")
        barrier = Barrier(2)

        def run(action):
            try:
                client = Client()
                client.cookies = self.client.cookies.copy()
                barrier.wait(timeout=10)
                if action == "confirm":
                    return self.confirm(draft, client=client).status_code
                return self.post(f"/api/purchase-orders/{self.po['id']}/void",
                                 {"revision": self.po["revision"], "reason": "Race"}, client).status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            confirm, void = pool.map(run, ("confirm", "void"))
        self.assertIn((confirm, void), [(201, 409), (409, 200)])
        self.assertEqual(Movement.objects.filter(kind="receipt").exists(), confirm == 201)

    def test_receipt_document_services_and_manual_opening_cutover(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from tempfile import TemporaryDirectory

        media = self.enterContext(TemporaryDirectory())
        self.enterContext(self.settings(MEDIA_ROOT=media))
        self.assertEqual(self.opening("manual", close=True).status_code, 201)
        self.assertEqual(self.opening("after-manual").json()["error"], "inventory_opening_closed")
        draft = self.receipt("1", "documents")
        root = f"/api/documents/grn/{draft['id']}"
        attached = self.client.post(root + "/attachments",
            {"file": SimpleUploadedFile("delivery.txt", b"GRN fixture")}, HTTP_IDEMPOTENCY_KEY="attachment")
        self.assertEqual(attached.status_code, 201, attached.content)
        archived = self.patch(root + "/archive", {"archived": True, "revision": draft["revision"]}).json()
        self.assertEqual(self.confirm({**draft, "revision": archived["revision"]}).json()["error"], "document_archived")
        restored = self.patch(root + "/archive", {"archived": False, "revision": archived["revision"]}).json()
        self.assertEqual(self.confirm({**draft, "revision": restored["revision"]}).status_code, 201)
        response = self.client.get(root + "/attachments/" + str(attached.json()["id"]))
        self.assertEqual(b"".join(response.streaming_content), b"GRN fixture")

    def test_unit_seed_preserves_existing_codes_and_is_safe_to_repeat(self):
        from importlib import import_module
        from django.apps import apps
        from django.db import connection

        from .models import Unit

        unit = Unit.objects.create(code="PC", name="Existing piece label")
        migration = import_module("inventory.migrations.0002_seed_common_units")
        with connection.schema_editor() as schema_editor:
            migration.seed_common_units(apps, schema_editor)
            migration.seed_common_units(apps, schema_editor)
        self.assertEqual(Unit.objects.get(code="PC").pk, unit.pk)
        self.assertEqual(Unit.objects.get(code="PC").name, "Existing piece label")
        self.assertEqual(set(Unit.objects.filter(code__in=["PC", "KG", "BOX"]).values_list("code", flat=True)),
                         {"PC", "KG", "BOX"})

    def test_audit_inactive_location_and_site_settings_protection(self):
        from inventory.models import Item, Location, Warehouse
        loc = Location.objects.select_related("warehouse").first()
        item = Item.objects.first()
        site_id = loc.warehouse.site_id

        # Deactivate location and verify opening rejects it
        res_loc = self.client.patch(f"/api/inventory/locations/{loc.pk}/state", {"active": False}, content_type="application/json")
        self.assertEqual(res_loc.status_code, 200)
        res_open = self.post("/api/inventory/opening", {
            "site_id": site_id,
            "lines": [{"item_id": item.pk, "location_id": loc.pk, "quantity": "5", "unit_cost": "10"}],
        }, HTTP_IDEMPOTENCY_KEY="audit-inv-open-1")
        self.assertEqual(res_open.status_code, 400)
        self.assertEqual(res_open.json()["error"], "invalid_location")

        # Deactivate warehouse and ensure location cannot be re-activated while warehouse is inactive
        wh = loc.warehouse
        res_wh = self.client.patch(f"/api/inventory/warehouses/{wh.pk}/state", {"active": False}, content_type="application/json")
        self.assertEqual(res_wh.status_code, 200)
        res_react = self.client.patch(f"/api/inventory/locations/{loc.pk}/state", {"active": True}, content_type="application/json")
        self.assertEqual(res_react.status_code, 409)
        self.assertEqual(res_react.json()["error"], "invalid_warehouse")

        # Restore warehouse & location, close opening, and verify SiteSettings cannot be deleted
        self.client.patch(f"/api/inventory/warehouses/{wh.pk}/state", {"active": True}, content_type="application/json")
        self.client.patch(f"/api/inventory/locations/{loc.pk}/state", {"active": True}, content_type="application/json")
        self.post("/api/inventory/opening", {"site_id": site_id, "lines": [], "close": True}, HTTP_IDEMPOTENCY_KEY="audit-inv-open-close-1")
        res_del_site = self.client.delete(f"/api/inventory/setup/site/{site_id}")
        self.assertEqual(res_del_site.status_code, 409)
