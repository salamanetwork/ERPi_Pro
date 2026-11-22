import datetime
from django.contrib.auth import get_user_model
from django.test import TestCase

from backbone import mfa
from backbone.models import Assignment, Authenticator, ModuleState, Role, Site
from hr.models import Employee
from projects.models import Membership, Project


class CommandRegistryTests(TestCase):
    def test_commands_requires_auth(self):
        response = self.client.get("/api/commands")
        self.assertEqual(response.status_code, 401)

    def test_commands_filters_by_permissions_and_query(self):
        User = get_user_model()
        ModuleState.objects.update_or_create(module_id="accounting", defaults={"enabled": True})
        staff = User.objects.create_user("cmd-staff", password="Password123!")
        self.client.force_login(staff)

        res = self.client.get("/api/commands")
        self.assertEqual(res.status_code, 200)
        ids = {row["id"] for row in res.json()["items"]}
        self.assertIn("nav.workspace", ids)
        self.assertNotIn("nav.modules", ids)
        self.assertNotIn("nav.data_import", ids)
        self.assertNotIn("nav.accounting", ids)

        filtered = self.client.get("/api/commands?q=settings")
        self.assertEqual(filtered.status_code, 200)
        self.assertTrue(any(row["id"] == "nav.settings" for row in filtered.json()["items"]))

        arabic_filtered = self.client.get("/api/commands?q=إعدادات")
        self.assertEqual(arabic_filtered.status_code, 200)
        self.assertTrue(any(row["id"] == "nav.settings" for row in arabic_filtered.json()["items"]))

    def test_site_scoped_user_only_sees_records_from_assigned_site(self):
        ModuleState.objects.update_or_create(module_id="hr", defaults={"enabled": True})
        ModuleState.objects.update_or_create(module_id="projects", defaults={"enabled": True})

        site_a = Site.objects.create(name="Site Alpha")
        site_b = Site.objects.create(name="Site Beta")

        Employee.objects.create(number="EMP-101", name="Alice Alpha", site=site_a, joined=datetime.date(2026, 1, 1))
        Employee.objects.create(number="EMP-202", name="Bob Beta", site=site_b, joined=datetime.date(2026, 1, 1))

        prj_a = Project.objects.create(code="PRJ-101", name="Alpha Build", start=datetime.date(2026, 1, 1), end=datetime.date(2026, 12, 31), site=site_a)
        Project.objects.create(code="PRJ-102", name="Alpha Unassigned", start=datetime.date(2026, 1, 1), end=datetime.date(2026, 12, 31), site=site_a)
        Project.objects.create(code="PRJ-202", name="Beta Build", start=datetime.date(2026, 1, 1), end=datetime.date(2026, 12, 31), site=site_b)

        role = Role.objects.create(name="Site Alpha Viewer", permissions=["hr.view", "projects.view"])
        site_user = get_user_model().objects.create_user("site-alpha-user", password="Password123!")
        Assignment.objects.create(user=site_user, role=role, site=site_a)
        Membership.objects.create(project=prj_a, user=site_user, role="member", active=True)
        self.client.force_login(site_user)

        emp_res = self.client.get("/api/commands?q=EMP-")
        self.assertEqual(emp_res.status_code, 200)
        emp_titles = [row["title"]["en"] for row in emp_res.json()["items"] if row["category"] == "record"]
        self.assertTrue(any("EMP-101" in t for t in emp_titles))
        self.assertFalse(any("EMP-202" in t for t in emp_titles))

        prj_res = self.client.get("/api/search?q=PRJ-")
        self.assertEqual(prj_res.status_code, 200)
        prj_numbers = {row["number"] for row in prj_res.json()["items"]}
        self.assertIn("PRJ-101", prj_numbers)
        self.assertNotIn("PRJ-102", prj_numbers)
        self.assertNotIn("PRJ-202", prj_numbers)

    def test_superuser_sees_admin_subroutes_and_live_records(self):
        ModuleState.objects.update_or_create(module_id="accounting", defaults={"enabled": True})
        ModuleState.objects.update_or_create(module_id="projects", defaults={"enabled": True})
        site = Site.objects.first() or Site.objects.create(name="Headquarters")
        Project.objects.create(code="PRJ-9001", name="Solar Grid Expansion", start=datetime.date(2026, 1, 1), end=datetime.date(2026, 12, 31), site=site)

        admin = get_user_model().objects.create_superuser("cmd-admin")
        self.client.force_login(admin)
        device = Authenticator.objects.create(user=admin, encrypted_key="test-session-stamp")
        session = self.client.session
        session[mfa.VERIFIED] = mfa.stamp(device)
        session.save()

        res = self.client.get("/api/commands")
        self.assertEqual(res.status_code, 200)
        ids = {row["id"] for row in res.json()["items"]}
        self.assertIn("nav.modules", ids)
        self.assertIn("nav.data_import", ids)
        self.assertIn("nav.users", ids)
        self.assertIn("nav.access_control", ids)
        self.assertIn("nav.accounting", ids)

        import_res = self.client.get("/api/commands?q=data%20import")
        self.assertEqual(import_res.status_code, 200)
        self.assertTrue(any(row["route"] == "/data-import" for row in import_res.json()["items"]))

        ar_import_res = self.client.get("/api/commands?q=استيراد")
        self.assertEqual(ar_import_res.status_code, 200)
        self.assertTrue(any(row["route"] == "/data-import" for row in ar_import_res.json()["items"]))

        rec_res = self.client.get("/api/commands?q=PRJ-9001")
        self.assertEqual(rec_res.status_code, 200)
        rec_items = [row for row in rec_res.json()["items"] if row["category"] == "record"]
        self.assertTrue(any(row["route"] == "/projects" and "PRJ-9001" in row["title"]["en"] for row in rec_items))
