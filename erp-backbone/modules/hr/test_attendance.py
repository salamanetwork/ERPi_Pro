from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import Client, TransactionTestCase

from assets.models import Asset
from backbone.models import Assignment, ModuleState, Role, Site
from inventory.models import SiteSettings
from workorders.models import MaintenanceJob, WorkOrder

from .models import AttendanceSession, Employee, Leave, WeeklyTimesheet, WorkOrderTimer


class AttendanceTests(TransactionTestCase):
    def setUp(self):
        self.site = Site.objects.create(name="Dubai")
        self.other = Site.objects.create(name="Other")
        SiteSettings.objects.create(site=self.site, timezone="Asia/Dubai")
        for key in ("hr", "assets", "workorders"):
            ModuleState.objects.create(module_id=key, enabled=True)
        self.worker = self.member("worker", self.site, ["attendance.record", "attendance.approve", "workorders.view",
            "maintenance.view", "maintenance.execute"])
        self.manager = self.member("manager", self.site, ["attendance.approve", "attendance.setup"])
        self.outsider = self.member("outsider", self.other, ["attendance.approve"])
        self.employee = Employee.objects.create(number="T1", name="Technician", site=self.site, user=self.worker,
            department="Service", title="Tech", joined=date(2025, 1, 1))
        asset = Asset.objects.create(name="Pump", tag="P1", site=self.site)
        self.orders = [WorkOrder.objects.create(title=f"Pump {i}", site=self.site, status="approved") for i in range(2)]
        for order in self.orders:
            MaintenanceJob.objects.create(order=order, asset=asset, stage="in_progress", assignee=self.worker)
        self.client.force_login(self.worker)
        self.sunday = datetime(2026, 11, 15, 18, tzinfo=UTC)

    def member(self, name, site, permissions):
        user = get_user_model().objects.create_user(username=name)
        role = Role.objects.create(name=name, permissions=permissions)
        Assignment.objects.create(user=user, role=role, site=site)
        return user

    def post(self, path, key, data=None, *, client=None):
        return (client or self.client).post("/api" + path, data or {}, content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=key)

    def at(self, hour=0, minutes=0):
        return patch("hr.attendance.event_time", return_value=self.sunday + timedelta(hours=hour, minutes=minutes))

    def start(self, order, session, key="start", *, client=None):
        return self.post(f"/work-orders/{order.pk}/timers/start", key, {"session_id": session}, client=client)

    def test_split_midnight_break_timers_and_approval(self):
        with self.at():
            first = self.post("/attendance/check-in", "check")
            self.assertEqual(first.status_code, 201, first.content)
            self.assertEqual(self.post("/attendance/check-in", "check").json(), first.json())
            self.assertEqual(self.post("/attendance/check-in", "another").status_code, 409)
            session = first.json()["session"]["id"]
        with self.at(1):
            started = self.start(self.orders[0], session)
            self.assertEqual(started.status_code, 201, started.content)
            timer = started.json()
            self.assertEqual(self.start(self.orders[0], session).json(), timer)
            self.assertEqual(self.start(self.orders[1], session).status_code, 409)
        with self.at(1, 45):
            self.assertEqual(self.post("/attendance/breaks/start", "break", {"session_id": session}).status_code, 201)
        with self.at(2, 15):
            self.assertEqual(self.post("/attendance/breaks/end", "end", {"session_id": session}).status_code, 201)
            other = self.start(self.orders[1], session, "start2").json()
        with self.at(3):
            stop_url = f"/work-orders/{self.orders[1].pk}/timers/{other['id']}/stop"
            stopped = self.post(stop_url, "stop")
            self.assertEqual(stopped.status_code, 201, stopped.content)
            self.assertEqual(self.post(stop_url, "stop").json(), stopped.json())
        with self.at(4):
            closed = self.post("/attendance/check-out", "out", {"session_id": session})
            self.assertEqual(closed.status_code, 201, closed.content)
            self.assertEqual(self.post("/attendance/check-out", "out", {"session_id": session}).json(), closed.json())
        self.assertEqual(WorkOrderTimer.objects.get(pk=timer["id"]).ended_at, self.sunday + timedelta(hours=1, minutes=45))
        old, new = WeeklyTimesheet.objects.order_by("starts_at")
        old_url = f"/attendance/timesheets/{old.pk}"
        with self.at(5):
            original = self.client.get("/api" + old_url).json()
            self.assertEqual(original["details"]["totals"]["net_seconds"], 6300)
            self.assertEqual(original["details"]["totals"]["wo_seconds"], 2700)
            second = self.client.get(f"/api/attendance/timesheets/{new.pk}").json()
            self.assertEqual(second["details"]["totals"]["net_seconds"], 6300)
            self.assertEqual(second["details"]["totals"]["wo_seconds"], 2700)
            submitted = self.post(old_url + "/submit", "", {"revision": 1})
            self.assertEqual(submitted.status_code, 200, submitted.content)
            self.assertEqual(self.post(old_url + "/approve", "", {"revision": 2}).status_code, 403)
            self.client.force_login(self.outsider)
            self.assertEqual(self.client.get("/api" + old_url).status_code, 404)
            self.client.force_login(self.manager)
            self.assertTrue(any(i["id"] == f"hr:timesheet:{old.pk}" for i in self.client.get("/api/inbox").json()["items"]))
            approved = self.post(old_url + "/approve", "", {"revision": 2})
            self.assertEqual(approved.status_code, 200, approved.content)
            self.assertEqual(approved.json()["details"], original["details"])
            self.assertFalse(any(i["id"] == f"hr:timesheet:{old.pk}" for i in self.client.get("/api/inbox").json()["items"]))

    def test_concurrent_starts_and_database_constraints(self):
        barrier = Barrier(2)

        def request_simultaneously(index, session=None):
            close_old_connections()
            try:
                client = Client()
                client.force_login(self.worker)
                barrier.wait(timeout=10)
                if session is None:
                    return self.post("/attendance/check-in", f"check-{index}", client=client).status_code
                return self.start(self.orders[index], session, f"order-{index}", client=client).status_code
            finally:
                connection.close()

        with self.at(), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(request_simultaneously, [0, 1]))
            self.assertEqual(sorted(results), [201, 409])
        shift = AttendanceSession.objects.get()
        with self.assertRaises(IntegrityError), transaction.atomic():
            AttendanceSession.objects.create(employee=self.employee, site=self.site, timezone="UTC", started_at=self.sunday)
        with self.at(1), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda index: request_simultaneously(index, shift.pk), [0, 1]))
            self.assertEqual(sorted(results), [201, 409])
        with self.assertRaises(IntegrityError), transaction.atomic():
            WorkOrderTimer.objects.create(employee=self.employee, session=shift, order=self.orders[0], started_at=self.sunday)
        with connection.cursor() as cursor:
            cursor.execute("SELECT indexdef FROM pg_indexes WHERE indexname IN (%s, %s)",
                           ["attendance_one_open_session", "attendance_one_open_timer"])
            definitions = [row[0] for row in cursor.fetchall()]
        self.assertEqual(len(definitions), 2)
        self.assertTrue(all("UNIQUE INDEX" in value and "WHERE (ended_at IS NULL)" in value for value in definitions))

    def test_approved_leave_and_policy_snapshots(self):
        with self.at():
            session = self.post("/attendance/check-in", "in").json()["session"]["id"]
        with self.at(1):
            self.post("/attendance/check-out", "out", {"session_id": session})
        original = WeeklyTimesheet.objects.get()
        self.client.force_login(self.manager)
        policy = self.client.patch(f"/api/attendance/sites/{self.site.pk}/settings",
            {"week_start": 6, "standard_minutes": 420, "revision": 0}, content_type="application/json")
        self.assertEqual(policy.status_code, 200, policy.content)
        self.client.force_login(self.worker)
        Leave.objects.create(employee=self.employee, site=self.site, start=date(2026, 11, 15),
            end=date(2026, 11, 15), paid=False, state="approved", requested_by=self.worker)
        with self.at(4):
            rejected = self.post(f"/attendance/timesheets/{original.pk}/submit", "", {"revision": 1})
            self.assertEqual(rejected.status_code, 409)
            self.assertTrue(self.client.get(f"/api/attendance/timesheets/{original.pk}").json()["details"]["leave_conflict"])
        original.refresh_from_db()
        self.assertEqual(original.standard_minutes, 480)
        self.assertEqual(original.week_start, date(2026, 11, 9))


    def test_complete_closes_timer_and_late_break_checkout_replays(self):
        with self.at():
            session = self.post("/attendance/check-in", "in").json()["session"]["id"]
        with self.at(1):
            timer = self.start(self.orders[0], session).json()
        with patch("workorders.maintenance.timezone.now", return_value=self.sunday + timedelta(hours=2)):
            completion = self.post(f"/maintenance/orders/{self.orders[0].pk}", "", {
                "action": "complete", "revision": self.orders[0].revision, "findings": "Done", "results": [],
            })
        self.assertEqual(completion.status_code, 200, completion.content)
        self.assertEqual(WorkOrderTimer.objects.get(pk=timer["id"]).ended_at, self.sunday + timedelta(hours=2))
        with self.at(2, 30):
            self.post("/attendance/breaks/start", "break", {"session_id": session})
        with self.at(3):
            self.post("/attendance/check-out", "out", {"session_id": session})
        shift = AttendanceSession.objects.get(pk=session)
        self.assertEqual(shift.breaks.get().ended_at, shift.ended_at)

    def test_standard_hours_and_dst_use_elapsed_utc(self):
        site = SiteSettings.objects.get(site=self.site)
        site.timezone = "America/New_York"
        site.save()
        self.sunday = datetime(2026, 11, 1, 4, tzinfo=UTC)
        self.client.force_login(self.manager)
        updated = self.client.patch(f"/api/attendance/sites/{self.site.pk}/settings",
            {"week_start": 6, "standard_minutes": 480, "revision": 0}, content_type="application/json")
        self.assertEqual(updated.status_code, 200)
        self.client.force_login(self.worker)
        with self.at():
            shift = self.post("/attendance/check-in", "in").json()["session"]["id"]
        with self.at(9):
            self.post("/attendance/check-out", "out", {"session_id": shift})
            sheet = WeeklyTimesheet.objects.get()
            result = self.client.get(f"/api/attendance/timesheets/{sheet.pk}").json()
        self.assertEqual(sheet.ends_at - sheet.starts_at, timedelta(hours=169))
        self.assertEqual(result["details"]["totals"]["net_seconds"], 32400)
        self.assertEqual(result["details"]["totals"]["eligible_overtime_seconds"], 3600)
