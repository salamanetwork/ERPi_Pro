from datetime import date
from decimal import Decimal
import json
from django.contrib.auth import get_user_model
from django.test import TestCase

from backbone.mfa import VERIFIED, stamp
from backbone.models import Assignment, Authenticator, CompanyProfile, ModuleState, Role, Site
from backbone.registry import MANIFESTS
from inventory.models import Item, Location, Position, Unit, Warehouse
from purchasing.models import PurchaseOrder, Requisition, Supplier
from sales.models import Customer, Document

User = get_user_model()


class DeletionPolicyAndTrashTests(TestCase):
    def setUp(self):
        for mod in MANIFESTS.keys():
            ModuleState.objects.update_or_create(module_id=mod, defaults={"enabled": True})

        self.admin = User.objects.create_superuser(
            username="admin_trash",
            email="admin_trash@example.com",
            password="StrongPassword123!",
        )
        self.site, _ = Site.objects.get_or_create(name="Main Site")
        CompanyProfile.objects.update_or_create(pk=1, defaults={"deletion_policy": "soft", "revision": 1})

        role = Role.objects.create(
            name="Full Admin Role",
            permissions=[
                "inventory.view",
                "inventory.setup",
                "sales.view",
                "sales.manage",
                "purchasing.view",
                "purchasing.manage",
                "suppliers.view",
                "suppliers.manage",
            ],
        )
        Assignment.objects.create(user=self.admin, role=role)
        Assignment.objects.create(site=self.site, user=self.admin, role=role)

        self.client.force_login(self.admin)
        device = Authenticator.objects.create(user=self.admin, encrypted_key="test-only-existing-device")
        session = self.client.session
        session[VERIFIED] = stamp(device)
        session.save()

    def _delete(self, url):
        return self.client.delete(url, HTTP_IDEMPOTENCY_KEY=f"del-{url}")

    def _post_json(self, url, payload=None):
        return self.client.post(
            url,
            data=json.dumps(payload or {}),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=f"post-{url}",
        )

    def test_soft_delete_and_restore_across_modules(self):
        unit = Unit.objects.create(code="PCS-T", name="Pieces Test")
        unit_free = Unit.objects.create(code="BOX-T", name="Box Free Test")
        item = Item.objects.create(code="ITM-T1", name="Test Pump", base_unit=unit)
        customer = Customer.objects.create(code="CUST-T1", name="Acme Trading")
        supplier = Supplier.objects.create(code="SUP-T1", name="Gulf Supplies")
        wh = Warehouse.objects.create(code="WH-T1", name="Main Store", site=self.site)
        loc = Location.objects.create(warehouse=wh, code="LOC-T1", name="Shelf A")

        self.assertEqual(self._delete(f"/api/inventory/items/{item.pk}").status_code, 200)
        self.assertEqual(self._delete(f"/api/sales/customers/{customer.pk}").status_code, 200)
        self.assertEqual(self._delete(f"/api/suppliers/{supplier.pk}").status_code, 200)
        self.assertEqual(self._delete(f"/api/inventory/setup/location/{loc.pk}").status_code, 200)
        self.assertEqual(self._delete(f"/api/inventory/setup/warehouse/{wh.pk}").status_code, 200)
        self.assertEqual(self._delete(f"/api/inventory/setup/unit/{unit_free.pk}").status_code, 200)

        items_res = self.client.get("/api/inventory/items").json()
        self.assertFalse(any(r["code"] == "ITM-T1" for r in items_res.get("rows", [])))

        custs_res = self.client.get("/api/sales/customers").json()
        self.assertFalse(any(r["code"] == "CUST-T1" for r in custs_res.get("rows", [])))

        supps_res = self.client.get("/api/suppliers").json()
        self.assertFalse(any(r["code"] == "SUP-T1" for r in supps_res.get("rows", [])))

        trash_res = self.client.get("/api/trash").json()
        trash_pairs = {(r["kind"], r["code"]) for r in trash_res["records"]}
        self.assertIn(("item", "ITM-T1"), trash_pairs)
        self.assertIn(("customer", "CUST-T1"), trash_pairs)
        self.assertIn(("supplier", "SUP-T1"), trash_pairs)
        self.assertIn(("warehouse", "WH-T1"), trash_pairs)
        self.assertIn(("location", "LOC-T1"), trash_pairs)
        self.assertIn(("unit", "BOX-T"), trash_pairs)

        for kind, pk in [
            ("unit", unit_free.pk),
            ("warehouse", wh.pk),
            ("location", loc.pk),
            ("item", item.pk),
            ("customer", customer.pk),
            ("supplier", supplier.pk),
        ]:
            r = self._post_json(f"/api/trash/{kind}/{pk}/restore")
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.json()["restored"])

        item.refresh_from_db()
        customer.refresh_from_db()
        supplier.refresh_from_db()
        self.assertFalse(item.is_deleted)
        self.assertTrue(item.active)
        self.assertFalse(customer.is_deleted)
        self.assertFalse(supplier.is_deleted)

    def test_dependency_guards_block_deletion(self):
        unit = Unit.objects.create(code="PCS-G", name="Guard Unit")
        item = Item.objects.create(code="ITM-G1", name="Guarded Item", base_unit=unit)
        wh = Warehouse.objects.create(code="WH-G1", name="Guard WH", site=self.site)
        loc = Location.objects.create(warehouse=wh, code="LOC-G1", name="Guard Loc")
        Position.objects.create(site=self.site, item=item, location=loc, quantity=5)

        res_item = self._delete(f"/api/inventory/items/{item.pk}")
        self.assertEqual(res_item.status_code, 409)

        res_unit = self._delete(f"/api/inventory/setup/unit/{unit.pk}")
        self.assertEqual(res_unit.status_code, 409)

        customer = Customer.objects.create(code="CUST-G1", name="Guarded Customer")
        Document.objects.create(
            kind="quotation",
            site=self.site,
            customer=customer,
            date=date(2026, 11, 1),
            status="draft",
            actor="admin_trash",
        )
        res_cust = self._delete(f"/api/sales/customers/{customer.pk}")
        self.assertEqual(res_cust.status_code, 409)

        supplier = Supplier.objects.create(code="SUP-G1", name="Guarded Supplier")
        req = Requisition.objects.create(
            number="PR-G1",
            site=self.site,
            title="Guard PR",
            currency="AED",
            lines=[],
            total=Decimal("100.00"),
            status="approved",
        )
        PurchaseOrder.objects.create(
            number="PO-G1",
            source_pr=req,
            site=self.site,
            supplier=supplier,
            title="Guard PO",
            currency="AED",
            lines=[],
            total=Decimal("100.00"),
        )
        res_supp = self._delete(f"/api/suppliers/{supplier.pk}")
        self.assertEqual(res_supp.status_code, 409)

    def test_permanent_policy_hard_deletes(self):
        CompanyProfile.objects.filter(pk=1).update(deletion_policy="permanent")
        customer = Customer.objects.create(code="CUST-PERM", name="Permanent Delete Cust")
        res = self._delete(f"/api/sales/customers/{customer.pk}")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["mode"], "permanent")
        self.assertFalse(Customer.objects.filter(pk=customer.pk).exists())
