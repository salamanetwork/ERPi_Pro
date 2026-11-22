from django.contrib.auth import get_user_model
from django.test import TestCase

from purchasing.models import Supplier
from backbone.mfa import VERIFIED, stamp
from backbone.models import Authenticator


class IndependentModulesTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_superuser(username="administrator")
        device = Authenticator.objects.create(user=user, encrypted_key="test-session-only")
        self.client.force_login(user)
        session = self.client.session
        session[VERIFIED] = stamp(device)
        session.save()

    def set_enabled(self, module, enabled=True):
        return self.client.patch(f"/api/modules/{module}", {"enabled":enabled}, content_type="application/json")

    def test_dependencies_and_gated_endpoints(self):
        self.assertEqual(self.set_enabled("purchase_orders").status_code, 409)
        self.assertEqual(self.set_enabled("purchasing").status_code, 200)
        self.assertEqual(self.client.get("/api/suppliers").status_code, 404)
        self.assertEqual(self.set_enabled("suppliers").status_code, 200)
        self.assertEqual(self.set_enabled("purchase_orders").status_code, 200)
        self.assertEqual(self.set_enabled("suppliers", False).status_code, 409)
        self.assertEqual(self.set_enabled("supplier_invoices").status_code, 409)
        self.assertEqual(self.set_enabled("inventory").status_code, 200)
        self.assertEqual(self.set_enabled("accounting").status_code, 200)
        self.assertEqual(self.set_enabled("supplier_invoices").status_code, 200)
        self.assertEqual(self.client.get("/api/ap/invoices").status_code, 200)
        self.assertEqual(self.set_enabled("purchase_orders", False).status_code, 409)
        self.assertEqual(self.set_enabled("supplier_invoices", False).status_code, 200)
        self.assertEqual(self.client.get("/api/ap/invoices").status_code, 404)
        self.assertEqual(self.set_enabled("purchase_orders", False).status_code, 200)
        self.assertEqual(self.client.get("/api/purchase-orders").status_code, 404)
        self.assertEqual(self.client.get("/api/purchase-requests").status_code, 200)
        self.assertEqual(self.set_enabled("sales").status_code, 409)
        self.assertEqual(self.set_enabled("customers").status_code, 200)
        self.assertEqual(self.client.get("/api/sales/customers?page=1").status_code, 200)
        self.assertEqual(self.set_enabled("sales").status_code, 200)
        self.assertEqual(self.set_enabled("customers", False).status_code, 409)

    def test_directory_paging_and_export_use_same_filters(self):
        self.set_enabled("suppliers")
        for code, name in [("AAA","One"), ("BBB","Two"), ("CCC","Three")]:
            Supplier.objects.create(code=code, name=name, currency_default="AED")
        endpoint = "/api/suppliers?directory=1&q=T&page=1&page_size=1"
        page = self.client.get(endpoint).json()
        self.assertEqual(page["count"], 2)
        self.assertEqual(page["results"][0]["name"], "Two")
        self.assertEqual(self.client.get(page["next"]).json()["results"][0]["name"], "Three")
        csv = self.client.get("/api/export/supplier?q=T")
        self.assertEqual(csv.status_code, 200)
        self.assertIn(b"Three", csv.content)
        self.assertNotIn(b"One", csv.content)
        self.set_enabled("suppliers", False)
        self.assertEqual(self.client.get(endpoint).status_code, 404)
        self.assertEqual(Supplier.objects.count(), 3)
