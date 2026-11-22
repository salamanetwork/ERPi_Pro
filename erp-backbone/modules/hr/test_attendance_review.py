from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from threading import Barrier
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import Client, SimpleTestCase, TransactionTestCase

from backbone.http import Problem
from backbone.models import Assignment, ModuleState, Role, Site
from inventory.models import SiteSettings
from workorders.models import WorkOrder

from .attendance_review import local_instant
from .models import AttendanceBreak, AttendanceCorrection, AttendanceSession, Employee, TimesheetApprovalHistory, WeeklyTimesheet, WorkOrderTimer


class LocalInstantTests(SimpleTestCase):
    def test_repeated_and_skipped_local_hours(self):
        zone = ZoneInfo("America/New_York")
        for wall, code in (("2026-11-01T01:30", "attendance_ambiguous_time"),
                           ("2026-03-08T02:30-05:00", "attendance_invalid_local_time"),
                           ("2026-11-01T01:30-06:00", "attendance_invalid_local_time")):
            with self.subTest(wall=wall), self.assertRaises(Problem) as rejected:
                local_instant(wall, zone)
            self.assertEqual(rejected.exception.code, code)
        self.assertEqual(local_instant("2026-11-01T01:30-04:00", zone), "2026-11-01T05:30:00+00:00")
        self.assertEqual(local_instant("2026-11-01T01:30-05:00", zone), "2026-11-01T06:30:00+00:00")


class AttendanceReviewTests(TransactionTestCase):
    def setUp(self):
        site = Site.objects.create(name="Dubai")
        SiteSettings.objects.create(site=site, timezone="Asia/Dubai")
        ModuleState.objects.create(module_id="hr", enabled=True)
        self.worker = self.member("worker", site, ["attendance.record", "attendance.approve",
                                                    "attendance.correct", "attendance.reopen"])
        rights = ["attendance.approve", "attendance.correct", "attendance.reopen"]
        self.manager = self.member("manager", site, rights)
        self.second = self.member("second", site, rights)
        self.outsider = self.member("outsider", Site.objects.create(name="Other"), rights)
        self.employee = Employee.objects.create(number="A", name="Worker", site=site, user=self.worker,
                                               department="Service", title="Tech", joined=date(2025, 1, 1))
        self.now = datetime(2026, 11, 18, tzinfo=UTC)
        self.start = datetime(2026, 11, 10, 4, tzinfo=UTC)
        self.shift = AttendanceSession.objects.create(employee=self.employee, site=site, timezone="Asia/Dubai",
                                                      started_at=self.start, ended_at=self.start + timedelta(hours=9))
        self.sheet = self.week(9)
        self.sheet_url = f"/attendance/timesheets/{self.sheet.pk}"
        self.correction_url = f"/attendance/sessions/{self.shift.pk}/corrections"
        self.clock = patch("hr.attendance.timezone.now", return_value=self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def week(self, day, **kwargs):
        start = datetime(2026, 11, day, tzinfo=UTC) - timedelta(hours=4)
        return WeeklyTimesheet.objects.create(employee=self.employee, site=self.employee.site,
            week_start=date(2026, 11, day), starts_at=start, ends_at=start + timedelta(days=7),
            timezone="Asia/Dubai", standard_minutes=480, **kwargs)

    def member(self, username, site, permissions):
        user = get_user_model().objects.create_user(username=username)
        role = Role.objects.create(name=username, permissions=permissions)
        Assignment.objects.create(user=user, role=role, site=site)
        return user

    def send(self, user, url, data, key=None, client=None):
        client = client or self.client
        client.force_login(user)
        headers = {"HTTP_IDEMPOTENCY_KEY": key} if key else {}
        return client.post("/api" + url, data, content_type="application/json", **headers)

    def day_minutes(self, minutes, reason):
        return {"2026-11-10": {"minutes": minutes, "reason": reason}}

    def simultaneous(self, path, payload):
        barrier = Barrier(2)

        def go(user):
            close_old_connections()
            try:
                client = Client()
                barrier.wait(timeout=10)
                return self.send(user, path, payload, client=client).status_code
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            return sorted(pool.map(go, [self.manager, self.second]))

    def test_overtime_validation_approval_reopen_and_version_history(self):
        invalid = self.send(self.worker, self.sheet_url + "/submit",
                            {"revision": 1, "overtime": self.day_minutes(61, "Callout")})
        self.assertEqual(invalid.status_code, 400)
        submitted = self.send(self.worker, self.sheet_url + "/submit",
                              {"revision": 1, "overtime": self.day_minutes(60, "Callout")})
        self.assertEqual(submitted.status_code, 200, submitted.content)
        decision = self.send(self.manager, self.sheet_url + "/approve",
                             {"revision": 2, "overtime": self.day_minutes(45, "")})
        self.assertEqual(decision.status_code, 400)
        decision = self.send(self.manager, self.sheet_url + "/approve",
                             {"revision": 2, "overtime": self.day_minutes(45, "15 minutes not authorized")})
        self.assertEqual(decision.status_code, 200, decision.content)
        self.assertEqual(decision.json()["details"]["overtime"]["decisions"]["2026-11-10"]["minutes"], 45)
        payload = {"revision": 3, "reason": "Verify attendance"}
        self.assertEqual(self.send(self.worker, self.sheet_url + "/reopen", payload).status_code, 403)
        self.assertEqual(self.send(self.outsider, self.sheet_url + "/reopen", payload).status_code, 404)
        self.assertEqual(self.simultaneous(self.sheet_url + "/reopen", payload), [200, 409])
        archived = TimesheetApprovalHistory.objects.get()
        self.assertEqual(archived.snapshot["overtime"]["decisions"]["2026-11-10"]["minutes"], 45)
        self.assertEqual(self.send(self.worker, self.sheet_url + "/submit", {"revision": 4}).status_code, 200)
        self.assertEqual(self.simultaneous(self.sheet_url + "/approve",
            {"revision": 5, "overtime": self.day_minutes(60, "")}), [200, 409])
        archived.refresh_from_db()
        self.assertEqual(archived.snapshot["overtime"]["decisions"]["2026-11-10"]["minutes"], 45)

    def test_missing_checkout_pending_guard_idempotency_and_concurrent_decision(self):
        self.shift.ended_at = None
        self.shift.save()
        data = {"ended_at": "2026-11-10T17:00", "reason": "Forgot check-out"}
        requested = self.send(self.worker, self.correction_url, data, "request")
        self.assertEqual(requested.status_code, 201, requested.content)
        self.assertEqual(self.send(self.worker, self.correction_url, data, "request").json(), requested.json())
        correction = AttendanceCorrection.objects.get()
        decision = f"/attendance/corrections/{correction.pk}/decision"
        payload = {"revision": 1, "action": "approve"}
        self.assertEqual(self.send(self.worker, decision, payload).status_code, 403)
        self.assertEqual(self.send(self.outsider, decision, payload).status_code, 404)
        self.assertEqual(self.send(self.worker, self.sheet_url + "/submit", {"revision": 1}).status_code, 409)
        self.client.force_login(self.manager)
        self.assertTrue(any(i["id"] == f"hr:correction:{correction.pk}" for i in self.client.get("/api/inbox").json()["items"]))
        self.assertEqual(self.simultaneous(decision, payload), [200, 409])
        self.shift.refresh_from_db()
        self.assertEqual(self.shift.ended_at, self.start + timedelta(hours=9))

    def test_cross_week_legacy_lock_and_timer_bounds(self):
        self.shift.started_at = self.sheet.ends_at - timedelta(hours=1)
        self.shift.ended_at = self.sheet.ends_at + timedelta(hours=2)
        self.shift.save()
        second = self.week(16, state="approved", snapshot={"days": [], "totals": {}})
        data = {"ended_at": "2026-11-16T01:00", "reason": "Earlier exit"}
        blocked = self.send(self.worker, self.correction_url, data, "locked")
        self.assertEqual(blocked.json()["error"], "attendance_correction_locked_weeks")
        reopened = self.send(self.manager, f"/attendance/timesheets/{second.pk}/reopen",
                             {"revision": 1, "reason": "Cross-week correction"})
        self.assertEqual(reopened.status_code, 200, reopened.content)
        self.assertNotIn("overtime", reopened.json()["history"][0]["snapshot"])
        order = WorkOrder.objects.create(title="WO", site=self.employee.site)
        WorkOrderTimer.objects.create(employee=self.employee, session=self.shift, order=order,
                                     started_at=self.sheet.ends_at, ended_at=self.shift.ended_at)
        blocked = self.send(self.worker, self.correction_url, data, "timer")
        self.assertEqual(blocked.json()["error"], "attendance_corrected_timer")
        self.assertFalse(AttendanceCorrection.objects.exists())
        WorkOrderTimer.objects.all().delete()
        request = self.send(self.worker, self.correction_url, data, "after-reopen")
        self.assertEqual(request.status_code, 201, request.content)
        decision = self.send(self.manager, f"/attendance/corrections/{request.json()['id']}/decision",
                             {"revision": 1, "action": "approve"})
        self.assertEqual(decision.status_code, 200, decision.content)
        self.assertEqual(AttendanceSession.objects.get(pk=self.shift.pk).ended_at, self.sheet.ends_at + timedelta(hours=1))

    def test_corrected_break_requires_overtime_request_to_match_new_limit(self):
        pause = AttendanceBreak.objects.create(session=self.shift, started_at=self.start + timedelta(hours=4),
                                               ended_at=self.start + timedelta(hours=4))
        saved = self.send(self.worker, self.sheet_url + "/overtime",
                          {"revision": 1, "overtime": self.day_minutes(60, "Callout")})
        self.assertEqual(saved.status_code, 200, saved.content)
        request = self.send(self.worker, self.correction_url,
            {"breaks": [{"id": pause.pk, "ended_at": "2026-11-10T12:15"}], "reason": "Break"}, "fix-break")
        self.assertEqual(request.status_code, 201, request.content)
        decision = self.send(self.manager, f"/attendance/corrections/{request.json()['id']}/decision",
                             {"revision": 1, "action": "approve"})
        self.assertEqual(decision.status_code, 200, decision.content)
        invalid = self.send(self.worker, self.sheet_url + "/submit", {"revision": 2})
        self.assertEqual(invalid.json()["error"], "invalid_attendance_overtime")
        valid = self.send(self.worker, self.sheet_url + "/submit",
                          {"revision": 2, "overtime": self.day_minutes(45, "Callout")})
        self.assertEqual(valid.status_code, 200, valid.content)

    def test_changed_shift_cannot_be_overwritten_by_pending_request(self):
        requested = self.send(self.worker, self.correction_url,
                              {"ended_at": "2026-11-10T16:00", "reason": "Correction"}, "stale")
        self.assertEqual(requested.status_code, 201)
        self.shift.ended_at += timedelta(minutes=10)
        self.shift.save()
        path = f"/attendance/corrections/{requested.json()['id']}/decision"
        stale = self.send(self.manager, path, {"revision": 1, "action": "approve"})
        self.assertEqual(stale.json()["error"], "attendance_correction_stale")
        rejected = self.send(self.manager, path, {"revision": 1, "action": "reject", "reason": "Changed"})
        self.assertEqual(rejected.status_code, 200)
