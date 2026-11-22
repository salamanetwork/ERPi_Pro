from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.views.decorators.cache import never_cache

from accounting.views import day_field
from backbone.extensions import invoke_extension
from backbone.access import audit, has_permission
from backbone.http import Problem, body, endpoint, identifier, once, require, string
from backbone.models import Site
from backbone.registry import enabled_modules
from inventory.models import SiteSettings
from workorders.maintenance import get_job

from .access import allowed_rows, one, own_or_scoped
from .models import (AttendanceBreak, AttendancePolicy, AttendanceSession, Employee, Leave,
                     TimesheetApprovalHistory, WeeklyTimesheet, WorkOrderTimer)


def site_timezone(site_id):
    return SiteSettings.objects.filter(site_id=site_id).values_list("timezone", flat=True).first() or "UTC"


def policy(site_id):
    return AttendancePolicy.objects.filter(site_id=site_id).first() or AttendancePolicy(site_id=site_id, revision=0)


def midnight(day, zone):
    return datetime.combine(day, time.min, tzinfo=zone).astimezone(UTC)


def ensure_week(employee, instant, timezone_name, settings):
    rows = WeeklyTimesheet.objects.filter(employee=employee, site_id=employee.site_id)
    current = rows.filter(starts_at__lte=instant, ends_at__gt=instant).first()
    if current:
        return current
    zone = ZoneInfo(timezone_name)
    day = instant.astimezone(zone).date()
    nominal = day - timedelta(days=(day.weekday() - settings.week_start) % 7)
    start, end = midnight(nominal, zone), midnight(nominal + timedelta(days=7), zone)
    previous = rows.filter(ends_at__lte=instant, ends_at__gt=start).order_by("-ends_at").first()
    following = rows.filter(starts_at__gt=instant, starts_at__lt=end).order_by("starts_at").first()
    start = previous.ends_at if previous else start
    end = following.starts_at if following else end
    return WeeklyTimesheet.objects.create(employee=employee, site_id=employee.site_id,
        week_start=start.astimezone(zone).date(), starts_at=start, ends_at=end,
        timezone=timezone_name, standard_minutes=settings.standard_minutes)


def my_employee(user):
    employee = getattr(user, "employee", None)
    if employee is None:
        raise Problem("attendance_employee_required", 403)
    require(user, "attendance.record", employee.site_id)
    return employee


def lock_employee(employee):
    current = Employee.objects.select_for_update().get(pk=employee.pk)
    if current.user_id != employee.user_id:
        raise Problem("attendance_employee_required", 403)
    return current


def active_session(employee):
    return AttendanceSession.objects.filter(employee=employee, ended_at__isnull=True).first()


def event_time():
    return timezone.now().astimezone(UTC).replace(microsecond=0)


def span(row):
    return {"id": row.pk, "started_at": row.started_at.isoformat(),
            "ended_at": row.ended_at.isoformat() if row.ended_at else None}


def state(employee):
    session = active_session(employee)
    pause = session and session.breaks.filter(ended_at__isnull=True).first()
    timer = session and session.timers.filter(ended_at__isnull=True).first()
    return {"site_id": employee.site_id, "timezone": session.timezone if session else site_timezone(employee.site_id),
            "session": span(session) if session else None, "break": span(pause) if pause else None,
            "timer": {**span(timer), "order_id": timer.order_id} if timer else None}


@never_cache
@endpoint("GET", module="hr")
def status(request):
    return state(my_employee(request.user))


@never_cache
@endpoint("GET", "PATCH", module="hr")
def settings(request, pk):
    require(request.user, "attendance.setup", pk)
    if request.method == "PATCH":
        data = body(request)
        week_start, minutes = data.get("week_start"), data.get("standard_minutes")
        if type(week_start) is not int or not 0 <= week_start <= 6 or type(minutes) is not int or not 1 <= minutes <= 1440:
            raise Problem("invalid_attendance_settings")
        with transaction.atomic():
            one(Site.objects.select_for_update().filter(active=True), pk)
            current = policy(pk)
            if type(data.get("revision")) is not int or data["revision"] != current.revision:
                raise Problem("revision_conflict", 409)
            if AttendanceSession.objects.filter(site_id=pk, ended_at__isnull=True).exists():
                raise Problem("attendance_open_session", 409)
            current.week_start, current.standard_minutes = week_start, minutes
            current.revision += 1
            current.save()
            audit(request.user, "attendance.settings", pk)
    else:
        one(Site.objects.all(), pk)
        current = policy(pk)
    return {"site_id": pk, "week_start": current.week_start, "standard_minutes": current.standard_minutes,
            "timezone": site_timezone(pk), "revision": current.revision}


def close_order_timers(order_id, instant, actor):
    # All order timer start/stop and lifecycle transitions lock the order first.
    for timer in WorkOrderTimer.objects.filter(order_id=order_id, ended_at__isnull=True):
        Employee.objects.select_for_update().get(pk=timer.employee_id)
        if WorkOrderTimer.objects.filter(pk=timer.pk, ended_at__isnull=True).update(ended_at=instant.replace(microsecond=0)):
            audit(actor, "attendance.timer.auto_stop", timer.pk)


def presence(request, action):
    employee = my_employee(request.user)
    data = body(request)

    def change():
        if action == "check_in":
            one(Site.objects.select_for_update().filter(active=True), employee.site_id)
        current = lock_employee(employee)
        now = event_time()
        session = active_session(current)
        if action == "check_in":
            if session:
                raise Problem("attendance_open_session", 409)
            name = site_timezone(current.site_id)
            today = now.astimezone(ZoneInfo(name)).date()
            if today < current.joined or current.ended and today > current.ended:
                raise Problem("attendance_employee_inactive", 409)
            week = ensure_week(current, now, name, policy(current.site_id))
            if week.state != "draft":
                raise Problem("attendance_week_locked", 409)
            session = AttendanceSession.objects.create(employee=current, site_id=current.site_id,
                                                       timezone=week.timezone, started_at=now)
        else:
            if not session or identifier(data.get("session_id"), "session") != session.pk:
                raise Problem("attendance_session_conflict", 409)
            if action in ("check_out", "start_break"):
                session.timers.filter(ended_at__isnull=True).update(ended_at=now)
            pause = session.breaks.filter(ended_at__isnull=True).first()
            if action == "start_break":
                if pause:
                    raise Problem("attendance_break_conflict", 409)
                AttendanceBreak.objects.create(session=session, started_at=now)
            else:
                if action == "end_break" and not pause:
                    raise Problem("attendance_break_conflict", 409)
                if pause:
                    pause.ended_at = now
                    pause.save(update_fields=["ended_at"])
                if action == "check_out":
                    session.ended_at = now
                    session.save(update_fields=["ended_at"])
                    # Materialize every touched week with its own immutable UTC bounds.
                    point = session.started_at
                    week_settings = policy(current.site_id)
                    while point < now:
                        point = ensure_week(current, point, session.timezone, week_settings).ends_at
        audit(request.user, "attendance." + action, session.pk)
        return state(current)

    try:
        return once(request, "attendance." + action, data, change)
    except IntegrityError as error:
        if getattr(error.__cause__.diag, "constraint_name", None) in ("attendance_one_open_session", "attendance_one_open_break"):
            raise Problem("attendance_open_session", 409) from None
        raise


@never_cache
@endpoint("POST", module="hr")
def check_in(request):
    return presence(request, "check_in")


@never_cache
@endpoint("POST", module="hr")
def check_out(request):
    return presence(request, "check_out")


@never_cache
@endpoint("POST", module="hr")
def start_break(request):
    return presence(request, "start_break")


@never_cache
@endpoint("POST", module="hr")
def end_break(request):
    return presence(request, "end_break")


def seconds(start, end):
    return max(0, int((end - start).total_seconds()))


@never_cache
@endpoint("GET", module="hr")
def order_timers(request, pk):
    if "workorders" not in enabled_modules():
        raise Problem("module_disabled", 404)
    order, _ = get_job(request.user, pk)
    rows = WorkOrderTimer.objects.filter(order=order).select_related("employee")
    now = event_time()
    totals = {}
    for row in rows:
        key = row.employee_id
        entry = totals.setdefault(key, {"employee": row.employee.name, "seconds": 0})
        entry["seconds"] += seconds(row.started_at, row.ended_at or now)
    employee = getattr(request.user, "employee", None)
    active = rows.filter(employee=employee, ended_at__isnull=True).first() if employee else None
    return {"totals": list(totals.values()), "open_timer": span(active) if active else None}


def timer_action(request, pk, timer_id=None):
    employee = my_employee(request.user)
    data = body(request)
    action = "stop" if timer_id else "start"

    def change():
        order, job = get_job(request.user, pk, lock=True)
        current = lock_employee(employee)
        now = event_time()
        if timer_id:
            row = one(WorkOrderTimer.objects.filter(order=order, employee=current), timer_id)
            if row.ended_at:
                raise Problem("attendance_timer_closed", 409)
            row.ended_at = now
            row.save(update_fields=["ended_at"])
        else:
            require(request.user, "maintenance.execute", order.site_id)
            if current.ended and now.astimezone(ZoneInfo(site_timezone(current.site_id))).date() > current.ended:
                raise Problem("attendance_employee_inactive", 409)
            if order.site_id != current.site_id or job.assignee_id != request.user.pk or job.stage != "in_progress":
                raise Problem("attendance_timer_unavailable", 409)
            session = active_session(current)
            if not session or identifier(data.get("session_id"), "session") != session.pk or session.breaks.filter(ended_at__isnull=True).exists():
                raise Problem("attendance_session_conflict", 409)
            if WorkOrderTimer.objects.filter(employee=current, ended_at__isnull=True).exists():
                raise Problem("attendance_open_timer", 409)
            row = WorkOrderTimer.objects.create(employee=current, session=session, order=order, started_at=now)
        audit(request.user, "attendance.timer." + action, row.pk)
        return {**span(row), "order_id": pk}

    if "workorders" not in enabled_modules():
        raise Problem("module_disabled", 404)
    # Check route access before looking up a replay receipt.
    get_job(request.user, pk)
    try:
        return once(request, "attendance.timer." + action, {"order_id": pk, "timer_id": timer_id, "data": data}, change)
    except IntegrityError as error:
        if getattr(error.__cause__.diag, "constraint_name", None) == "attendance_one_open_timer":
            raise Problem("attendance_open_timer", 409) from None
        raise


@never_cache
@endpoint("POST", module="hr")
def start_timer(request, pk):
    return timer_action(request, pk)


@never_cache
@endpoint("POST", module="hr")
def stop_timer(request, pk, timer_id):
    return timer_action(request, pk, timer_id)


def weekly_projection(sheet, now):
    zone = ZoneInfo(sheet.timezone)
    end = min(sheet.ends_at, now)
    sessions = AttendanceSession.objects.filter(employee_id=sheet.employee_id, site_id=sheet.site_id,
        started_at__lt=end).filter(Q(ended_at__isnull=True) | Q(ended_at__gt=sheet.starts_at)).prefetch_related(
            "breaks", "timers").order_by("started_at")
    leave_rows = list(Leave.objects.filter(employee_id=sheet.employee_id, site_id=sheet.site_id, state="approved",
        start__lte=(sheet.ends_at - timedelta(microseconds=1)).astimezone(zone).date(),
        end__gte=sheet.week_start))
    rows = []
    days = []
    point = sheet.starts_at
    while point < sheet.ends_at:
        day = point.astimezone(zone).date()
        following = min(midnight(day + timedelta(days=1), zone), sheet.ends_at)
        days.append({"day": day.isoformat(), "seconds": 0, "break_seconds": 0, "wo_seconds": 0,
                     "has_leave": any(l.start <= day <= l.end for l in leave_rows)})
        point = following
    by_day = {day["day"]: day for day in days}

    def distribute(start, stop, field):
        point = max(start, sheet.starts_at)
        stop = min(stop, end)
        while point < stop:
            local = point.astimezone(zone).date()
            following = min(midnight(local + timedelta(days=1), zone), stop)
            by_day[local.isoformat()][field] += seconds(point, following)
            point = following

    open_session = False
    for session in sessions:
        open_session |= session.ended_at is None
        distribute(session.started_at, session.ended_at or now, "seconds")
        breaks = []
        timers = []
        for pause in session.breaks.all():
            if pause.started_at < end and (pause.ended_at is None or pause.ended_at > sheet.starts_at):
                distribute(pause.started_at, pause.ended_at or now, "break_seconds")
                breaks.append(span(pause))
        for timer in session.timers.all():
            if timer.started_at < end and (timer.ended_at is None or timer.ended_at > sheet.starts_at):
                distribute(timer.started_at, timer.ended_at or now, "wo_seconds")
                timers.append({**span(timer), "order_id": timer.order_id,
                    "seconds": seconds(max(timer.started_at, sheet.starts_at), min(timer.ended_at or now, end))})
        rows.append({**span(session), "breaks": breaks, "timers": timers})
    keys = ("seconds", "break_seconds", "wo_seconds", "net_seconds", "unassigned_seconds", "eligible_overtime_seconds")
    for day in days:
        day["net_seconds"] = day["seconds"] - day["break_seconds"]
        day["unassigned_seconds"] = day["net_seconds"] - day["wo_seconds"]
        day["eligible_overtime_seconds"] = max(0, day["net_seconds"] - sheet.standard_minutes * 60)
        day["leave_conflict"] = day["has_leave"] and day["seconds"] > 0
    return {"days": days, "sessions": rows, "totals": {key: sum(day[key] for day in days) for key in keys},
            "open_session": open_session, "leave_conflict": any(day["leave_conflict"] for day in days)}


def visible_sheets(user):
    rows = WeeklyTimesheet.objects.select_related("employee")
    return (own_or_scoped(rows, user, "attendance.approve", "attendance.record", "employee__user_id")
            | allowed_rows(rows, user, "attendance.reopen"))


def sheet_summary(row):
    return {"id": row.pk, "employee": row.employee.name, "site_id": row.site_id,
            "week_start": row.week_start.isoformat(), "starts_at": row.starts_at.isoformat(),
            "ends_at": row.ends_at.isoformat(), "timezone": row.timezone,
            "standard_minutes": row.standard_minutes, "state": row.state, "revision": row.revision,
            "review_note": row.review_note}


@never_cache
@endpoint("GET", "POST", module="hr")
def sheets(request):
    if request.method == "POST":
        employee = my_employee(request.user)
        data = body(request)
        day = day_field(data, "week_start")

        def prepare():
            one(Site.objects.select_for_update().filter(active=True), employee.site_id)
            current = lock_employee(employee)
            zone = site_timezone(current.site_id)
            today = event_time().astimezone(ZoneInfo(zone)).date()
            if day > today or day < today - timedelta(days=366):
                raise Problem("invalid_week_start")
            existing = WeeklyTimesheet.objects.filter(employee=current, week_start=day).order_by("starts_at").first()
            if existing:
                return sheet_summary(existing)
            config = policy(current.site_id)
            if day.weekday() != config.week_start or not Leave.objects.filter(employee=current, state="approved",
                    start__lt=day + timedelta(days=7), end__gte=day).exists():
                raise Problem("attendance_week_unavailable", 409)
            row = ensure_week(current, midnight(day, ZoneInfo(zone)), zone, config)
            audit(request.user, "attendance.timesheet.prepare", row.pk)
            return sheet_summary(row)

        return once(request, "attendance.timesheet.prepare", data, prepare)
    rows = visible_sheets(request.user)
    week = request.GET.get("week_start")
    site = request.GET.get("site_id")
    state_filter = request.GET.get("status")
    if week:
        rows = rows.filter(week_start=day_field(request.GET, "week_start"))
    if site:
        if not site.isascii() or not site.isdecimal() or len(site) > 18:
            raise Problem("invalid_site")
        rows = rows.filter(site_id=int(site))
    if state_filter:
        if state_filter not in ("draft", "submitted", "approved"):
            raise Problem("invalid_status")
        rows = rows.filter(state=state_filter)
    if "page_size" in request.GET:
        from backbone.services.pagination import operational_page
        return operational_page(request,rows.order_by('-starts_at','-pk'),sheet_summary,['employee__name','state','site__name'])
    return {"items": [sheet_summary(row) for row in rows.order_by("-starts_at", "-pk")[:100]]}


def sheet_detail(row, user):
    from .attendance_review import pending_for_week

    owns = row.employee.user_id == user.pk
    pending = pending_for_week(row) if row.state == "draft" else False
    overtime = row.snapshot.get("overtime") if row.state != "draft" else None
    active_projects = "projects" in enabled_modules()
    project_time = invoke_extension("timesheet_detail",user,row) if active_projects else []
    project_review = not active_projects or owns or invoke_extension(
        "timesheet_reviewable",WeeklyTimesheet.objects.filter(pk=row.pk),user).exists()
    return {**sheet_summary(row), "owns": owns, "project_time":project_time, "can_record": owns and has_permission(user, "attendance.record", row.site_id),
            "correction_pending": pending, "overtime_requests": row.overtime_requests,
            "overtime_reviewed": None if row.state != "approved" else overtime is not None,
            "can_reopen": project_review and not owns and row.submitted_by_id != user.pk and row.state == "approved"
            and has_permission(user, "attendance.reopen", row.site_id),
            "history": [{"revision": version.approved_revision, "snapshot": version.snapshot,
                         "approved_by": version.approved_by.get_username() if version.approved_by else None,
                         "approved_at": version.approved_at, "reopened_by": version.reopened_by.get_username(),
                         "reopened_at": version.reopened_at, "reason": version.reason}
                        for version in row.approval_history.select_related("approved_by", "reopened_by").order_by("-approved_revision")],
            "reviewed_at": row.reviewed_at, "can_submit": owns and row.state == "draft" and row.ends_at <= event_time()
            and has_permission(user, "attendance.record", row.site_id),
            "can_review": project_review and not owns and row.submitted_by_id != user.pk and row.state == "submitted"
            and has_permission(user, "attendance.approve", row.site_id),
            "details": row.snapshot if row.state != "draft" else weekly_projection(row, event_time())}


@never_cache
@endpoint("GET", module="hr")
def sheet(request, pk):
    return sheet_detail(one(visible_sheets(request.user), pk), request.user)


@never_cache
@endpoint("POST", module="hr")
def sheet_transition(request, pk, action):
    from .attendance_review import overtime_decisions, overtime_requests, pending_for_week

    user = request.user
    data = body(request)
    with transaction.atomic():
        initial = one(visible_sheets(user), pk)
        employee = Employee.objects.select_for_update().get(pk=initial.employee_id)
        row = WeeklyTimesheet.objects.select_for_update().get(pk=pk)
        if identifier(data.get("revision"), "revision") != row.revision:
            raise Problem("revision_conflict", 409)
        now = event_time()
        if action == "overtime":
            require(user, "attendance.record", row.site_id)
            if employee.user_id != user.pk:
                raise Problem("forbidden", 403)
            if row.state != "draft":
                raise Problem("attendance_week_locked", 409)
            row.overtime_requests = overtime_requests(data.get("overtime"), weekly_projection(row, now))
        elif action == "reopen":
            require(user, "attendance.reopen", row.site_id)
            if user.pk in (employee.user_id, row.submitted_by_id):
                raise Problem("hr_separate_reviewer", 403)
            if row.state != "approved":
                raise Problem("attendance_week_unavailable", 409)
            reason = string(data, "reason", 500)
            TimesheetApprovalHistory.objects.create(sheet=row, approved_revision=row.revision,
                snapshot={**row.snapshot, "review_note": row.review_note}, approved_by_id=row.reviewed_by_id,
                approved_at=row.reviewed_at,
                reopened_by=user, reopened_at=now, reason=reason)
            row.state, row.snapshot, row.review_note = "draft", {}, reason
            row.reviewed_at, row.reviewed_by = None, None
        elif action == "submit":
            require(user, "attendance.record", row.site_id)
            if employee.user_id != user.pk:
                raise Problem("forbidden", 403)
            if row.state != "draft" or now < row.ends_at:
                raise Problem("attendance_week_unavailable", 409)
            projection = weekly_projection(row, now)
            if projection["open_session"] or projection["leave_conflict"] or pending_for_week(row):
                raise Problem("attendance_week_conflict", 409)
            row.overtime_requests = overtime_requests(data.get("overtime", row.overtime_requests), projection)
            row.snapshot, row.state, row.submitted_by = projection, "submitted", user
        else:
            require(user, "attendance.approve", row.site_id)
            if user.pk in (employee.user_id, row.submitted_by_id):
                raise Problem("hr_separate_reviewer", 403)
            if row.state != "submitted":
                raise Problem("attendance_week_unavailable", 409)
            row.review_note = string(data, "reason", 500, required=action == "return")
            if action == "approve":
                projection = weekly_projection(row, now)
                if projection["open_session"] or projection["leave_conflict"] or projection != row.snapshot:
                    raise Problem("attendance_week_conflict", 409)
                decisions = overtime_decisions(data.get("overtime", {}), row.overtime_requests)
                if "overtime" in data or row.overtime_requests:
                    row.snapshot = {**row.snapshot, "overtime": {"requests": row.overtime_requests, "decisions": decisions}}
                row.state = "approved"
            else:
                row.state, row.snapshot = "draft", {}
            row.reviewed_at, row.reviewed_by = now, user
        if action in ("submit","approve","return","reopen"):
            invoke_extension("timesheet_transition",row,user,action)
        row.revision += 1
        row.save()
        audit(user, "attendance.timesheet." + action, row.pk)
        return sheet_detail(row, user)


def ensure_leave_can_be_approved(leave):
    # The caller holds the employee row lock, shared with weekly review.
    sheets = WeeklyTimesheet.objects.filter(employee_id=leave.employee_id, state="approved",
        week_start__gte=leave.start - timedelta(days=7), week_start__lte=leave.end)
    for sheet in sheets:
        for day in sheet.snapshot.get("days", []):
            if leave.start.isoformat() <= day["day"] <= leave.end.isoformat() and not day["has_leave"]:
                raise Problem("attendance_week_locked", 409)


def pending_reviews(user, site=None):
    rows = allowed_rows(WeeklyTimesheet.objects.filter(state="submitted"), user, "attendance.approve")
    rows = rows.exclude(Q(employee__user=user) | Q(submitted_by=user))
    if "projects" in enabled_modules():
        rows = invoke_extension("timesheet_reviewable",rows,user)
    return rows if site is None else rows.filter(site_id=site)
