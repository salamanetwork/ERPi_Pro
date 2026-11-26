from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

from django.contrib.auth import get_user_model
from django.db import connections, transaction
from django.test import Client, TransactionTestCase

from backbone.mfa import VERIFIED, stamp
from backbone.models import Assignment, AuditEvent, Authenticator, ModuleState, Role, Site
from purchasing.models import PurchaseOrder, Requisition
from purchasing.orders import register_receipt


class PurchaseOrderFixture:
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
        for module in ("purchasing", "purchase_orders", "suppliers"):
            ModuleState.objects.update_or_create(module_id=module, defaults={"enabled": True})
        self.buyer = self.employee("buyer", self.east, "create")
        self.issuer = self.employee("issuer", self.east, "issue", "void")
        self.outside = self.employee("outside", self.west, "create", "issue", "void")
        self.supplier_fields = {"name": "Pump Services", "code": "SUP1", "legal_name": "Pump Services LLC",
                                "currency_default": "USD", "tax_id": "TEST-123", "active": True}
        self.supplier = self.post("/api/suppliers", self.supplier_fields).json()
        self.pr = self.source(self.east)

    def employee(self, name, site, *permissions):
        user = get_user_model().objects.create_user(username=name)
        role = Role.objects.create(name=name, permissions=["purchasing.view",
                                                           *[f"purchasing.po.{p}" for p in permissions]])
        Assignment.objects.create(user=user, role=role, site=site)
        client = Client()
        client.force_login(user)
        return client

    def source(self, site, status="approved"):
        return Requisition.objects.create(title="Pump seal", site=site, status=status, currency="AED",
            lines=[{"description": "Seal", "quantity": "3.000", "unit_price": "33.3333", "amount": "100.00"}],
            total=Decimal("100.00"))

    def post(self, url, payload, client=None, **headers):
        return (client or self.client).post(url, payload, content_type="application/json", **headers)

    def patch(self, url, payload, client=None):
        return (client or self.client).patch(url, payload, content_type="application/json")

    def convert(self, *, source=None, key="convert", client=None, supplier=None):
        pk = (source or self.pr).pk
        return self.post(f"/api/purchase-requests/{pk}/purchase-order",
                         {"supplier_id": supplier or self.supplier["id"]}, client, HTTP_IDEMPOTENCY_KEY=key)


class PurchaseOrderTests(PurchaseOrderFixture, TransactionTestCase):
    def test_scoped_conversion_approved_and_numbering(self):
        path = f"/api/purchase-requests/{self.pr.pk}"
        self.assertTrue(self.buyer.get(path).json()["can_create_purchase_order"])
        self.assertFalse(self.issuer.get(path).json()["can_create_purchase_order"])
        self.assertEqual(self.convert(client=self.outside).status_code, 404)
        self.assertEqual(self.convert(client=self.issuer).status_code, 403)
        for status in ("draft", "in_review"):
            self.assertEqual(self.convert(source=self.source(self.east, status), client=self.buyer).json()["error"],
                             "purchase_request_not_approved")
        config = self.patch("/api/numbering-rules/po", {"prefix": "PO", "per_site": True, "start": 1, "revision": 1})
        self.assertEqual(config.json()["error"], "company_numbering_required")
        response = self.convert(client=self.buyer)
        self.assertEqual(response.status_code, 201, response.content)
        row = response.json()
        self.assertEqual(self.issuer.get("/api/inbox?kind=po").json()["items"][0]["number"], row["number"])
        self.assertEqual(self.buyer.get("/api/inbox?kind=po").json()["items"], [])
        self.assertEqual((row["number"], row["site_id"], row["currency"], row["lines"][0]["id"]),
                         ("PO-000001", self.east.pk, "AED", 1))
        self.assertEqual(self.convert(client=self.buyer).json(), row)
        self.assertEqual(self.convert(client=self.buyer, supplier=999).json()["error"], "idempotency_conflict")
        self.assertEqual(self.convert(key="again", client=self.buyer).json()["error"], "purchase_request_already_converted")
        other = self.convert(source=self.source(self.west), key="west").json()
        self.assertEqual(other["number"], "PO-000002")
        self.assertEqual(len(self.buyer.get("/api/purchase-orders").json()["items"]), 1)
        self.assertEqual(self.outside.get(f"/api/purchase-orders/{row['id']}").status_code, 404)

    def test_prices_issue_and_void_pin_snapshot(self):
        row = self.convert().json()
        path = f"/api/purchase-orders/{row['id']}"
        patch = {"revision": row["revision"], "supplier_id": row["supplier_id"],
                 "prices": [{"id": 1, "unit_price": "33.3350"}]}
        self.assertEqual(self.patch(path, patch, self.buyer).json()["error"], "invalid_price_change_reason")
        changed = self.patch(path, {**patch, "price_change_reason": "Quoted price"}, self.buyer).json()
        self.assertEqual(changed["total"], "100.01")
        self.assertEqual(changed["price_changes"][0]["changes"][0]["delta"], "0.0017")
        self.assertEqual(self.patch(path, patch, self.buyer).status_code, 409)
        self.assertEqual(self.post(path + "/issue", {"revision": changed["revision"]}, self.buyer).status_code, 403)
        issued = self.post(path + "/issue", {"revision": changed["revision"]}, self.issuer).json()
        snapshot = issued["snapshot"]
        self.assertEqual(snapshot["supplier"]["legal_name"], "Pump Services LLC")
        self.assertEqual(snapshot["lines"][0]["amount"], "100.01")
        self.assertEqual(snapshot["issued_by"], "issuer")
        self.assertEqual(self.patch(path, {**patch, "revision": issued["revision"]}, self.buyer).status_code, 409)
        self.assertEqual(self.post(path + "/void", {"revision": issued["revision"]}, self.issuer).status_code, 400)
        response = self.patch(f"/api/suppliers/{row['supplier_id']}", {
            **self.supplier_fields, "name": "Renamed", "revision": 1, "active": False,
        })
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.convert(source=self.source(self.east), key="inactive").json()["error"], "supplier_unavailable")
        self.assertContains(self.buyer.get(f"/print/po/{row['id']}?language=ar"), "Pump Services LLC")
        voided = self.post(path + "/void", {"revision": issued["revision"], "reason": "Order withdrawn"}, self.issuer).json()
        self.assertEqual(voided["status"], "cancelled")
        self.assertEqual(voided["snapshot"], snapshot)
        self.assertEqual(voided["voided_by"], "issuer")
        self.assertEqual(self.convert(key="replacement").json()["error"], "purchase_request_already_converted")
        self.assertTrue(AuditEvent.objects.filter(action="purchase_order.void",
                        target=f"{row['number']} / {self.pr.document_number} / rev {voided['revision']}").exists())

    def test_two_conversions_and_receipt_void_guard(self):
        barrier = Barrier(2)

        def try_conversion(index):
            try:
                client = Client()
                client.cookies = self.buyer.cookies.copy()
                barrier.wait(timeout=10)
                return self.convert(client=client, key=f"race-{index}").status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(try_conversion, range(2))), [201, 409])
        row = PurchaseOrder.objects.get(source_pr=self.pr)
        path = f"/api/purchase-orders/{row.pk}"
        issued = self.post(path + "/issue", {"revision": row.revision}, self.issuer).json()
        with transaction.atomic():
            link = register_receipt(row.pk, "fixture:GRN-1")
            self.assertEqual(register_receipt(row.pk, "fixture:GRN-1").pk, link.pk)
        denied = self.post(path + "/void", {"revision": issued["revision"], "reason": "test"}, self.issuer)
        self.assertEqual(denied.json()["error"], "purchase_order_has_receipts")
        self.assertEqual(self.buyer.get(path).json()["status"], "issued")
        self.assertEqual(self.buyer.get("/api/inbox").json()["items"], [])

    def test_receipt_and_void_are_serialized(self):
        row = self.convert().json()
        path = f"/api/purchase-orders/{row['id']}"
        issued = self.post(path + "/issue", {"revision": row["revision"]}, self.issuer).json()
        start = Barrier(2)

        def receive():
            from backbone.http import Problem

            try:
                start.wait(timeout=10)
                register_receipt(row["id"], "fixture:GRN-race")
                return True
            except Problem as failure:
                self.assertEqual(failure.code, "purchase_order_not_issued")
                return False
            finally:
                connections.close_all()

        def void_order():
            try:
                client = Client()
                client.cookies = self.issuer.cookies.copy()
                start.wait(timeout=10)
                return self.post(path + "/void", {"revision": issued["revision"], "reason": "Race test"}, client)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            receipt = pool.submit(receive)
            result = pool.submit(void_order).result(timeout=10)
            received = receipt.result(timeout=10)
        if received:
            self.assertEqual(result.json()["error"], "purchase_order_has_receipts")
        else:
            self.assertEqual(result.json()["status"], "cancelled")
        self.assertEqual(PurchaseOrder.objects.get(pk=row["id"]).receipt_links.exists(), received)

    def test_archive_and_restore_draft_preserve_conversion(self):
        row = self.convert(client=self.buyer).json()
        path = f"/api/purchase-orders/{row['id']}"
        archive = f"/api/documents/po/{row['id']}/archive"
        result = self.patch(archive, {"archived": True, "revision": row["revision"]}, self.buyer)
        self.assertEqual(result.status_code, 200, result.content)
        revision = result.json()["revision"]
        self.assertEqual(self.buyer.get("/api/purchase-orders").json()["items"], [])
        self.assertEqual(self.buyer.get("/api/purchase-orders?archived=1").json()["items"][0]["number"], row["number"])
        self.assertEqual(self.post(path + "/issue", {"revision": revision}, self.issuer).json()["error"], "document_archived")
        self.assertEqual(self.convert(key="archived", client=self.buyer).json()["error"], "purchase_request_already_converted")
        self.assertEqual(self.patch(archive, {"archived": False, "revision": revision}, self.buyer).status_code, 200)
        self.assertEqual(self.buyer.get(path).json()["number"], row["number"])

    def test_audit_deleted_supplier_and_inactive_site_guards(self):
        from backbone.models import Site
        from purchasing.models import Supplier

        sup = Supplier.objects.create(code="DEL-SUP", name="Deleted Sup", currency_default="AED", active=True, is_deleted=True)
        res_conv = self.convert(supplier=sup.pk, key="audit-po-conv-del-sup", client=self.buyer)
        self.assertEqual(res_conv.status_code, 409)
        self.assertEqual(res_conv.json()["error"], "supplier_unavailable")

        po = self.convert(key="audit-po-conv-valid", client=self.buyer).json()
        Site.objects.filter(pk=self.east.pk).update(active=False)
        try:
            res_issue = self.post(f"/api/purchase-orders/{po['id']}/issue", {"revision": po["revision"]}, self.issuer)
            self.assertIn(res_issue.status_code, (403, 409))
        finally:
            Site.objects.filter(pk=self.east.pk).update(active=True)
