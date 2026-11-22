from datetime import date
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image

from hr.models import Employee

from .mfa import VERIFIED, stamp
from .models import Assignment, AuditEvent, Authenticator, ModuleState, Role, Site
from .navigation import SCREEN_IDS


class NavigationProfileTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.admin = users.objects.create_superuser("admin")
        cls.device = Authenticator.objects.create(user=cls.admin, encrypted_key="test-existing-device")
        cls.worker = users.objects.create_user("worker")
        cls.manager = users.objects.create_user("manager")
        cls.hr = users.objects.create_user("hr")
        cls.outsider = users.objects.create_user("outsider")
        cls.site = Site.objects.create(name="A")
        other = Site.objects.create(name="B")
        for user, location, permissions in (
            (cls.worker, cls.site, ["hr.self"]),
            (cls.manager, cls.site, ["hr.view"]),
            (cls.hr, cls.site, ["hr.manage"]),
            (cls.outsider, other, ["hr.manage", "hr.identity.view_full"]),
        ):
            Assignment.objects.create(user=user, site=location, role=Role.objects.create(
                name=user.username, permissions=permissions,
            ))
        ModuleState.objects.create(module_id="hr", enabled=True)
        cls.employee = Employee.objects.create(
            user=cls.worker, number="EMP-1", name="Employee", site=cls.site,
            department="Maintenance", title="Technician", joined=date(2025, 1, 1),
        )

    def login_admin(self):
        self.client.force_login(self.admin)
        session = self.client.session
        session[VERIFIED] = stamp(self.device)
        session.save()

    def test_navigation_settings_leave_module_states_and_routes_available(self):
        self.login_admin()
        hidden = ["module_manager", "access_control", "audit_trail", "document_settings"]
        self.assertEqual(self.client.patch("/api/navigation/settings", {"hidden_screens": hidden},
                                          content_type="application/json").status_code, 200)
        self.assertEqual(set(self.client.get("/api/workspace").json()["hidden_screens"]), set(hidden))
        self.assertTrue(ModuleState.objects.get(module_id="hr").enabled)
        for route in ("/modules", "/access", "/audit", "/document-settings", "/sales/customers", "/inventory/balances"):
            self.assertEqual(self.client.get(route).status_code, 200, route)
        self.assertEqual(self.client.get("/api/audit").status_code, 200)
        self.assertTrue(AuditEvent.objects.filter(action="navigation.visibility").exists())
        self.client.force_login(self.worker)
        self.assertEqual(self.client.get("/api/navigation/settings").status_code, 403)
        self.assertEqual(self.client.get("/api/audit").status_code, 403)

    def test_role_preserves_module_ids_and_supports_screen_ids(self):
        role = Role.objects.get(name="worker")
        self.login_admin()
        response = self.client.patch(f"/api/access/roles/{role.pk}/visibility",
                                     {"hidden_modules": ["employees", "inventory"]}, content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        self.client.force_login(self.worker)
        self.assertEqual(self.client.get("/api/workspace").json()["hidden_screens"], ["employees"])
        self.assertIn(self.employee.number, self.client.get("/api/hr/employees").content.decode())
        Assignment.objects.create(user=self.worker, site=self.site, role=Role.objects.create(name="second role"))
        self.assertEqual(self.client.get("/api/workspace").json()["hidden_screens"], [])
        self.assertEqual(len(SCREEN_IDS), len(set(SCREEN_IDS)))

    def test_personal_details_are_separate_scoped_masked_and_revision_checked(self):
        url = f"/api/hr/employees/{self.employee.pk}/personal"
        national_id = "TEST-123456789"
        profile = {"revision": 1, "birth_date": "1990-01-01", "nationality": "Egyptian",
                   "address": "Private street", "national_id": national_id}
        self.client.force_login(self.hr)
        result = self.client.patch(url, profile, content_type="application/json")
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(result.json()["national_id_masked"], "••••6789")
        self.assertNotIn("national_id", result.json())
        self.assertEqual(self.client.patch(url, profile, content_type="application/json").status_code, 409)
        self.employee.refresh_from_db()
        self.assertNotIn(national_id, self.employee.national_id_encrypted)
        self.assertEqual(AuditEvent.objects.get(action="hr.employee.personal.update").target, str(self.employee.pk))
        self.client.force_login(self.worker)
        self.assertEqual(self.client.get("/api/profile").json()["employee_id"], self.employee.pk)
        self.assertFalse(self.client.get(url).json()["can_edit"])
        self.assertEqual(self.client.patch(url, profile, content_type="application/json").status_code, 404)
        for user in (self.manager, self.outsider):
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 404)
            self.assertNotContains(self.client.get("/api/hr/employees"), "Private street")
        self.login_admin()
        self.assertNotIn("national_id", self.client.get(url).json())
        self.assertEqual(self.client.get(url + "?reveal=1").json()["national_id"], national_id)

    def test_picture_is_validated_normalized_and_private_to_the_session(self):
        url = "/api/profile/picture"
        self.client.force_login(self.worker)
        fake = SimpleUploadedFile("fake.png", b"<svg/>", content_type="image/png")
        self.assertEqual(self.client.post(url, {"picture": fake}).status_code, 400)
        image = BytesIO()
        Image.new("RGB", (500, 30)).save(image, "PNG")
        uploaded = self.client.post(url, {"picture": SimpleUploadedFile("photo.png", image.getvalue())})
        self.assertEqual(uploaded.status_code, 200, uploaded.content)
        picture_url = uploaded.json()["picture_url"]
        response = self.client.get(picture_url)
        self.assertEqual(response["Content-Type"], "image/jpeg")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(Image.open(BytesIO(response.content)).width, 384)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(picture_url).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(picture_url).status_code, 401)
        self.client.force_login(self.worker)
        self.assertEqual(self.client.delete(url).json(), {"picture_url": None})
        self.assertEqual(self.client.get(url).status_code, 404)
