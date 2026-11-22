from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connections
from django.test import Client, TransactionTestCase

from backbone.http import Problem
from backbone.mfa import VERIFIED, stamp
from backbone.models import Authenticator, Assignment, ModuleState, Role, Site
from inventory.models import SiteSettings
from .models import AttendanceSession, Employee, EmployeeNumbering, HRReference


class EmployeeDirectoryTests(TransactionTestCase):
    def setUp(self):
        ModuleState.objects.update_or_create(module_id="hr", defaults={"enabled": True})
        self.site = Site.objects.create(name="HQ")
        SiteSettings.objects.create(site=self.site, timezone="Asia/Dubai")
        users = get_user_model().objects
        self.admin = users.create_superuser(username="administrator")
        self.approver = users.create_user(username="reviewer")
        self.operator = users.create_user(username="employee")
        Assignment.objects.create(user=self.approver, site=self.site,
                                  role=Role.objects.create(name="Approver", permissions=["hr.offboarding.approve"]))
        Assignment.objects.create(user=self.operator, site=self.site,
                                  role=Role.objects.create(name="Attendance", permissions=["attendance.record"]))
        self.device = Authenticator.objects.create(user=self.admin, encrypted_key="test-session-only")
        self.login_admin(self.client)
        self.department = HRReference.objects.create(kind="departments", code="HR", name="HR")
        self.title = HRReference.objects.create(kind="job-titles", code="ADMIN", name="Admin")

    def login_admin(self, client):
        client.force_login(self.admin)
        session = client.session
        session[VERIFIED] = stamp(self.device)
        session.save()

    def create(self, name, key, *, client=None, **extra):
        data = {"name": name, "site_id": self.site.pk, "department_ref_id": self.department.pk,
                "title_ref_id": self.title.pk, "joined": "2026-01-01", **extra}
        return (client or self.client).post("/api/hr/employees", data, content_type="application/json",
                                            HTTP_IDEMPOTENCY_KEY=key)

    def test_counter_rolls_back_after_save_error_and_serializes_concurrent_creates(self):
        with patch("hr.people.audit", side_effect=Problem("test_failure")):
            self.assertEqual(self.create("Failed", "retry").status_code, 400)
        self.assertFalse(Employee.objects.exists())
        first = self.create("First", "retry")
        self.assertEqual(first.json()["number"], "10001", first.content)
        self.assertEqual(self.create("First", "retry").json()["number"], "10001")
        clients = [Client(), Client()]
        for client in clients:
            self.login_admin(client)
        ready = Barrier(2)
        other = Site.objects.create(name="Parallel site")
        department = HRReference.objects.create(kind="departments", code="OPS", name="Operations")
        title = HRReference.objects.create(kind="job-titles", code="OP", name="Operator")
        overrides = [{}, {"site_id":other.pk, "department_ref_id":department.pk, "title_ref_id":title.pk}]

        def create(index):
            ready.wait(timeout=10)
            try:
                result = self.create(f"Parallel {index}", f"parallel-{index}", client=clients[index], **overrides[index])
                return result.status_code, result.json()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(create, range(2)))
        self.assertEqual([status for status, _ in results], [201, 201], results)
        self.assertEqual({row["number"] for _, row in results}, {"10002", "10003"})
        self.assertEqual(EmployeeNumbering.objects.get().next_number, 10004)

    def test_paginated_directory_export_archival_and_department_scope(self):
        first = self.create("Match 1", "first").json()
        self.create("Match 2", "second")
        self.create("Other", "other")
        index = "/api/hr/employees"
        path = index + "?q=Match&page=1&page_size=1"
        result = self.client.get(path).json()
        self.assertEqual(result["count"], 2)
        self.assertIsNone(result["previous"])
        self.assertEqual([row["number"] for row in result["results"]], [first["number"]])
        second = self.client.get(result["next"]).json()
        self.assertEqual(second["page"], 2)
        self.assertIsNone(second["next"])
        self.assertIn(b"Match 2", self.client.get(index + "/export?q=Match").content)
        self.assertNotIn(b"Other", self.client.get(index + "/export?q=Match").content)
        self.assertEqual(self.client.post(f'{index}/{first["id"]}/archive',
            {"revision":first["revision"],"active":False}, content_type="application/json").status_code, 200)
        self.assertEqual(self.client.get(path).json()["count"], 1)
        self.assertEqual(self.client.get(index + "?status=archived&page=1").json()["count"], 1)
        other_site = Site.objects.create(name="Other site")
        Assignment.objects.create(user=self.operator, site=other_site,
                                  role=Role.objects.create(name="HR view", permissions=["hr.view","platform.export"]))
        reader = Client()
        reader.force_login(self.operator)
        self.assertEqual(reader.get(path).json()["count"], 0)
        self.assertNotIn(b"Match", reader.get(index + "/export?q=Match").content)

    def test_separate_offboarding_approval_and_last_day_site_boundary(self):
        employee = self.create("Active employee", "employee", user_id=self.operator.pk).json()
        path = "/api/hr/offboarding"
        response = self.client.post(path, {"employee_id":employee["id"],"kind":"resignation",
            "last_day":"2026-11-15","reason":"Notice"}, content_type="application/json", HTTP_IDEMPOTENCY_KEY="request")
        self.assertEqual(response.status_code, 201, response.content)
        request = response.json()
        decision = path + f'/{request["id"]}/decision'
        payload = {"action":"approve","revision":request["revision"]}
        self.assertEqual(self.client.post(decision, payload, content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="self").status_code, 403)
        reviewer = Client()
        reviewer.force_login(self.approver)
        approved = reviewer.post(decision, payload, content_type="application/json", HTTP_IDEMPOTENCY_KEY="approve")
        self.assertEqual(approved.status_code, 201, approved.content)
        self.assertEqual(Employee.objects.get(pk=employee["id"]).ended, date(2026, 11, 15))
        self.assertEqual(reviewer.post(decision, payload, content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="again").status_code, 409)
        worker = Client()
        worker.force_login(self.operator)
        with patch("hr.attendance.event_time", return_value=datetime(2026, 11, 15, 19, 30, tzinfo=UTC)):
            response = worker.post("/api/attendance/check-in", {}, content_type="application/json",
                                   HTTP_IDEMPOTENCY_KEY="last-day")
            self.assertEqual(response.status_code, 201, response.content)
        with patch("hr.attendance.event_time", return_value=datetime(2026, 11, 15, 19, 59, tzinfo=UTC)):
            response = worker.post("/api/attendance/check-out", {"session_id": AttendanceSession.objects.get().pk},
                                   content_type="application/json",
                                   HTTP_IDEMPOTENCY_KEY="last-exit")
            self.assertEqual(response.status_code, 201, response.content)
        with patch("hr.attendance.event_time", return_value=datetime(2026, 11, 15, 20, 0, tzinfo=UTC)):
            response = worker.post("/api/attendance/check-in", {}, content_type="application/json",
                                   HTTP_IDEMPOTENCY_KEY="after-last-day")
            self.assertEqual(response.status_code, 409, response.content)
            self.assertEqual(response.json()["error"], "attendance_employee_inactive")
