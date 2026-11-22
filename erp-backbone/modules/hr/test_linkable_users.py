from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase

from backbone.models import Assignment, ModuleState, Profile, Role, Site
from .models import Employee


class LinkableUsersTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.site = Site.objects.create(name="Dubai")
        cls.other = Site.objects.create(name="Other")
        ModuleState.objects.create(module_id="hr", enabled=True)
        users = get_user_model()
        cls.hr = users.objects.create_user("hr")
        cls.manager = users.objects.create_user("manager_test", first_name="Layla", last_name="Salama")
        Profile.objects.create(user=cls.manager, middle_name="Ahmed")
        cls.foreign = users.objects.create_user("manager_elsewhere")
        cls.attached = users.objects.create_user("manager_already_linked")
        cls.reader = users.objects.create_user("reader")
        reviewer = Role.objects.create(name="HR manage and link", permissions=["hr.manage", "hr.identity.link"])
        role = Role.objects.create(name="Member", permissions=["hr.self"])
        reader_role = Role.objects.create(name="HR manage", permissions=["hr.manage"])
        for user, site, assigned in (
            (cls.hr, cls.site, reviewer), (cls.manager, cls.site, role),
            (cls.foreign, cls.other, role), (cls.attached, cls.site, role), (cls.reader, cls.site, reader_role),
        ):
            Assignment.objects.create(user=user, site=site, role=assigned)
        cls.employee = Employee.objects.create(number="E01", name="Test", department="Operations",
            title="Manager", joined=date(2024, 1, 1), site=cls.site, user=cls.attached)

    def test_lookup_requires_both_permissions_and_respects_site_and_link(self):
        url = f"/api/hr/sites/{self.site.pk}/linkable-users"
        self.client.force_login(self.reader)
        self.assertEqual(self.client.get(url, {"q": "manager"}).status_code, 403)
        self.client.force_login(self.hr)
        self.assertEqual(self.client.get(f"/api/hr/sites/{self.other.pk}/linkable-users", {"q": "manager"}).status_code, 403)
        response = self.client.get(url, {"q": "manager"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"], [{
            "id": self.manager.pk, "username": "manager_test", "name": "Layla Ahmed Salama",
        }])
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(self.client.get(url, {"q": "Layla Salama"}).json()["items"], response.json()["items"])
        selected = self.client.get(url, {"employee": self.employee.pk}).json()
        self.assertEqual(selected["selected"]["username"], self.attached.username)
        self.assertFalse(selected["locked"])
        updated = self.client.patch(f"/api/hr/employees/{self.employee.pk}", {
            "name": self.employee.name, "department": self.employee.department, "title": self.employee.title,
            "joined": self.employee.joined.isoformat(), "site_id": self.site.pk, "revision": self.employee.revision,
            "user_id": self.manager.pk,
        }, content_type="application/json")
        self.assertEqual(updated.status_code, 200)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.user_id, self.manager.pk)
