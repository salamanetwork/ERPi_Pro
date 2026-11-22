from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.utils import timezone
import pyotp
from django.test import Client, TestCase

from .models import Assignment, AuditEvent, Role, Site


class BackboneTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.admin = users.objects.create_superuser(username="administrator", password="test-only-strong-password")
        self.operator = users.objects.create_user(username="operator", password="test-only-operator-password")
        self.site_a = Site.objects.create(name="North")
        self.site_b = Site.objects.create(name="South")
        self.client.force_login(self.admin)
        from .mfa import VERIFIED, stamp
        from .models import Authenticator

        device = Authenticator.objects.create(user=self.admin, encrypted_key="test-only-existing-device")
        session = self.client.session
        session[VERIFIED] = stamp(device)
        session.save()

    def toggle(self, module, enabled=True):
        return self.client.patch(f"/api/modules/{module}", {"enabled": enabled}, content_type="application/json")

    def create_order(self, key="create-1", **fields):
        return self.client.post("/api/work-orders", {"title": "Check equipment", **fields},
                                content_type="application/json", HTTP_IDEMPOTENCY_KEY=key)

    def create_asset(self, **fields):
        return self.client.post("/api/assets", {"name": "Pump", "tag": "P-001", **fields},
                                content_type="application/json", HTTP_IDEMPOTENCY_KEY="asset-1")

    def update_order(self, order_id, **fields):
        return self.client.patch(f"/api/work-orders/{order_id}", fields, content_type="application/json")

    def test_module_view_preference_persists(self):
        response = self.client.patch("/api/preferences/module-view", {"module_view": "grid"}, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/workspace").json()["module_view"], "grid")

    def test_fresh_workspace_and_independent_modules(self):
        modules = self.client.get("/api/workspace").json()["modules"]
        self.assertEqual({m["id"] for m in modules if m["enabled"]}, {"platform"})
        self.assertEqual(self.client.get("/api/assets").status_code, 404)
        self.assertEqual(self.client.get("/api/work-orders").status_code, 404)
        self.assertEqual(self.toggle("assets").status_code, 200)
        asset = self.create_asset()
        self.assertEqual(asset.status_code, 201)
        self.assertEqual(self.client.get("/api/work-orders").status_code, 404)
        self.toggle("assets", False)
        self.toggle("workorders")
        order = self.create_order()
        self.assertEqual(order.status_code, 201)
        self.assertIsNone(order.json()["asset_reference"])
        self.assertEqual(self.client.get("/api/assets").status_code, 404)

    def test_optional_link_survives_deactivation(self):
        self.toggle("assets")
        self.toggle("workorders")
        asset_id = self.create_asset().json()["id"]
        order_id = self.create_order(asset_reference=asset_id).json()["id"]
        self.toggle("assets", False)
        item = self.client.get(f"/api/work-orders/{order_id}").json()
        self.assertEqual(item["asset_snapshot"], "P-001 · Pump")
        self.assertEqual(self.create_order("second", asset_reference=asset_id).status_code, 409)
        self.assertEqual(self.update_order(order_id, title="Draft edited", revision=1).json()["asset_reference"], asset_id)
        self.toggle("assets")
        self.assertEqual(self.client.get("/api/assets").json()["items"][0]["id"], asset_id)
        self.assertTrue(AuditEvent.objects.filter(action="module.disable", target="assets").exists())

    def test_duplicate_request_and_stale_edit(self):
        self.toggle("workorders")
        first = self.create_order()
        replay = self.create_order()
        self.assertEqual((first.status_code, replay.status_code), (201, 200))
        self.assertEqual(first.json(), replay.json())
        self.assertEqual(self.create_order(title="Another request").status_code, 409)
        self.assertEqual(len(self.client.get("/api/work-orders").json()["items"]), 1)
        order_id = first.json()["id"]
        self.assertEqual(self.update_order(order_id, title="First edit", revision=1).status_code, 200)
        stale = self.update_order(order_id, title="Second edit", revision=1)
        self.assertEqual(stale.json()["error"], "revision_conflict")
        self.assertEqual(self.client.get(f"/api/work-orders/{order_id}").json()["title"], "First edit")
        submitted = self.client.post(f"/api/work-orders/{order_id}/submit", {"revision": 2},
                                     content_type="application/json")
        self.assertEqual(submitted.json()["status"], "submitted")
        self.assertEqual(self.update_order(order_id, title="Too late", revision=3).json()["error"], "not_draft")

    def test_site_scope_and_direct_endpoints(self):
        self.toggle("workorders")
        self.toggle("assets")
        assets = self.create_asset(site_id=self.site_b.pk).json()
        north = self.create_order(site_id=self.site_a.pk).json()
        south = self.create_order("other", site_id=self.site_b.pk).json()
        edit = Role.objects.create(name="Editor", permissions=["workorders.view", "workorders.create", "workorders.edit"])
        read = Role.objects.create(name="Reader", permissions=["workorders.view", "assets.view"])
        Assignment.objects.create(user=self.operator, role=edit, site=self.site_a)
        Assignment.objects.create(user=self.operator, role=read, site=self.site_b)
        self.client.force_login(self.operator)
        self.assertEqual(self.toggle("assets", False).status_code, 403)
        self.assertEqual(self.update_order(south["id"], title="Forbidden", revision=1,
                                          site_id=self.site_b.pk).status_code, 403)
        self.assertEqual(self.update_order(north["id"], title="North allowed", revision=1,
                                          site_id=self.site_a.pk).status_code, 200)
        self.assertEqual(self.create_order("link", site_id=self.site_a.pk, asset_reference=assets["id"]).status_code, 409)
        Assignment.objects.filter(user=self.operator, site=self.site_b).delete()
        self.assertEqual(self.client.get(f"/api/work-orders/{south['id']}").status_code, 404)
        self.assertEqual(len(self.client.get("/api/work-orders").json()["items"]), 1)
        self.assertEqual(self.client.get("/api/audit").status_code, 403)

    def test_login_csrf_and_preferences(self):
        client = Client(enforce_csrf_checks=True)
        credentials = {"username": "operator", "password": "test-only-operator-password"}
        self.assertEqual(client.get("/api/workspace").status_code, 401)
        self.assertEqual(client.post("/api/login", credentials, content_type="application/json").status_code, 403)
        client.get("/api/session")
        self.assertEqual(client.post("/api/login", credentials, content_type="application/json",
                                    HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value).status_code, 200)
        self.assertEqual(client.patch("/api/preferences", {"language": "ar", "theme": "dark"},
                                      content_type="application/json",
                                      HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value).status_code, 200)
        self.assertEqual(client.get("/api/workspace").json()["language"], "ar")
        self.assertEqual(client.get("/api/workspace").json()["theme"], "dark")


class SetupTests(TestCase):
    def test_setup_creates_admin_and_leaves_modules_disabled(self):
        from io import StringIO
        from unittest.mock import patch

        from django.core.management import call_command
        from .models import ModuleState

        with patch("backbone.management.commands.setup_backbone.getpass.getpass",
                   return_value="Test-only-bootstrap-password-17"):
            call_command("setup_backbone", username="test-admin", stdout=StringIO())
        self.assertTrue(get_user_model().objects.get(username="test-admin").is_superuser)
        from .registry import MANIFESTS
        self.assertSetEqual(set(ModuleState.objects.values_list("module_id", flat=True)),
                            {key for key,m in MANIFESTS.items() if m.get("category")!="platform_core"})
        self.assertFalse(ModuleState.objects.filter(enabled=True).exists())
        self.assertTrue(Role.objects.filter(name="Work order operator").exists())



class AuthenticatorTests(TestCase):
    password = "test-only-authentication-25"

    def setUp(self):
        self.user = get_user_model().objects.create_superuser(username="admin", password=self.password)
        self.client = Client(enforce_csrf_checks=True)
        self.client.get("/api/session")

    def post(self, path, data):
        return self.client.post(path, data, content_type="application/json",
                                HTTP_X_CSRFTOKEN=self.client.cookies["csrftoken"].value)

    def login(self):
        return self.post("/api/login", {"username": self.user.username, "password": self.password})

    def test_enrollment_replay_and_console_reset(self):
        from io import StringIO
        from django.core.management import call_command

        self.assertEqual(self.client.get("/api/workspace").status_code, 401)
        challenge = self.login().json()["mfa"]
        self.assertEqual(challenge["mode"], "enroll")
        self.assertEqual(self.client.get("/api/session").json()["mfa"], challenge)
        self.assertEqual(self.client.get("/api/workspace").status_code, 401)
        totp = pyotp.TOTP(challenge["setup_key"])
        now = timezone.now()
        code = totp.at(now)
        with patch("backbone.mfa.timezone.now", return_value=now):
            self.assertEqual(self.post("/api/login/mfa", {"code": code}).status_code, 200)
            self.assertEqual(self.client.get("/api/workspace").status_code, 200)
            self.assertEqual(self.post("/api/logout", {}).status_code, 200)
            self.assertEqual(self.login().json()["mfa"], {"mode": "verify"})
            self.assertEqual(self.post("/api/login/mfa", {"code": code}).json()["error"], "invalid_mfa_code")
            self.assertEqual(self.post("/api/login/mfa", {"code": totp.at(now, counter_offset=1)}).status_code, 200)
        call_command("reset_mfa", self.user.username, confirm=True, stdout=StringIO())
        self.assertEqual(self.client.get("/api/workspace").status_code, 401)
        self.assertEqual(self.login().json()["mfa"]["mode"], "enroll")

    def test_lock_persists_across_password_challenges(self):
        from .models import Authenticator

        for _ in range(5):
            self.login()
            with patch("backbone.mfa.pyotp.TOTP.generate_otp", return_value="000000"):
                response = self.post("/api/login/mfa", {"code": "999999"})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(self.login().status_code, 429)
        self.assertIsNotNone(Authenticator.objects.get(user=self.user).blocked_until)

    def test_new_sensitive_assignment_requires_mfa_on_existing_session(self):
        self.user.is_superuser = False
        self.user.is_staff = False
        self.user.save()
        self.assertTrue(self.login().json()["ok"])
        role = Role.objects.create(name="Auditor", permissions=["platform.audit"])
        Assignment.objects.create(role=role, user=self.user)
        self.assertEqual(self.client.get("/api/audit").status_code, 401)
        self.assertEqual(self.login().json()["mfa"]["mode"], "enroll")


class SPARoutingTests(TestCase):
    def test_module_deep_links_serve_the_shell(self):
        for url in ("/sales/customers", "/sales/quotations/", "/sales/123", "/inventory/receipts/123",
                    "/inventory/balances", "/accounting/periods", "/accounting/reports/trial",
                    "/purchase-orders/123", "/work-orders/nested/view", "/payroll/runs/123",
                    "/payroll/payslips", "/hr/payroll/123"):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'id="app"')

    def test_reserved_paths_do_not_fall_through_to_the_shell(self):
        for url in ("/api/sales/unknown", "/api/accounting/unknown", "/static/unknown.js", "/salesforce/customers"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get("/sales/print/123").json()["error"], "login_required")


class WorkspaceApplicationModeTests(TestCase):
    def test_mode_changes_workspace_metadata_only(self):
        user = get_user_model().objects.create_user(username="mode-reader")
        self.client.force_login(user)
        responses = {}

        for mode in ("development", "production"):
            with self.subTest(mode=mode), self.settings(ERP_APP_MODE=mode):
                response = self.client.get("/api/workspace")
                self.assertEqual(response.status_code, 200)
                workspace = response.json()
                self.assertEqual(workspace.pop("mode"), mode)
                self.assertFalse(workspace["admin"])
                responses[mode] = workspace

        self.assertEqual(responses["development"], responses["production"])


class FieldValidationContractTests(TestCase):
    def setUp(self):
        from .mfa import VERIFIED, stamp
        from .models import Authenticator, ModuleState
        self.admin = get_user_model().objects.create_superuser(username="validation-admin")
        device = Authenticator.objects.create(user=self.admin, encrypted_key="test-validation-device")
        self.client.force_login(self.admin)
        session = self.client.session
        session[VERIFIED] = stamp(device)
        session.save()
        for module in ("hr", "purchasing"):
            ModuleState.objects.update_or_create(pk=module, defaults={"enabled": True})
        self.site = Site.objects.create(name="Validation Site")

    def test_employee_validation_collects_independent_fields(self):
        response = self.client.post("/api/hr/employees", {
            "name": " ", "site_id": self.site.pk, "joined": "2026-02-30",
            "department_ref_id": None, "title_ref_id": None,
        }, content_type="application/json", HTTP_IDEMPOTENCY_KEY="employee-invalid")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_joined")
        self.assertEqual(response.json()["field_error_codes"], {
            "joined": ["invalid_date"], "name": ["required"],
            "department_ref_id": ["required"], "title_ref_id": ["required"],
        })
        self.assertEqual(response.json()["field_errors"]["name"], ["This field is required."])

    def test_purchase_validation_identifies_exact_lines_and_does_not_write(self):
        from purchasing.models import Requisition
        response = self.client.post("/api/purchase-requests", {
            "title": "Validation", "site_id": self.site.pk, "currency": "AED",
            "lines": [
                {"description": "Pump", "quantity": "1", "unit_price": "20"},
                {"description": "Seal", "quantity": "0", "unit_price": "20"},
                {"description": "Cable", "quantity": "1", "unit_price": "bad"},
            ],
        }, content_type="application/json", HTTP_IDEMPOTENCY_KEY="purchase-invalid")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_quantity")
        self.assertEqual(response.json()["field_error_codes"], {
            "lines.1.quantity": ["min_value"], "lines.2.unit_price": ["invalid_number"],
        })
        self.assertFalse(Requisition.objects.exists())

    def test_malformed_json_is_a_request_error(self):
        response = self.client.post("/api/hr/employees", "{", content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "invalid_json"})
