from datetime import UTC, datetime, timedelta
import re
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import Q
from django.views.decorators.cache import never_cache

from backbone.access import audit, has_permission
from backbone.http import Problem, body, endpoint, identifier, once, require, string
from .access import allowed_rows, one, own_or_scoped
from .attendance import ensure_week, event_time, lock_employee, my_employee, policy, seconds, span
from .models import AttendanceCorrection, AttendanceSession, Employee, WeeklyTimesheet


LOCAL_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(?::[0-9]{2})?(?:[+-][0-9]{2}:[0-9]{2})?")


def local_instant(value, zone):
    if not isinstance(value, str) or not LOCAL_TIME.fullmatch(value):
        raise Problem("invalid_attendance_local_time")
    try:
        supplied = datetime.fromisoformat(value)
        wall = supplied.replace(tzinfo=None)
        candidates = set()
        for fold in (0, 1):
            instant = wall.replace(tzinfo=zone, fold=fold).astimezone(UTC)
            local = instant.astimezone(zone)
            if local.replace(tzinfo=None) == wall and (supplied.tzinfo is None or supplied.utcoffset() == local.utcoffset()):
                candidates.add(instant)
    except (ValueError, OverflowError):
        raise Problem("invalid_attendance_local_time") from None
    if len(candidates) != 1:
        raise Problem("attendance_ambiguous_time" if candidates else "attendance_invalid_local_time")
    return candidates.pop().isoformat()


def session_snapshot(session):
    return {**span(session), "breaks": [span(row) for row in session.breaks.order_by("pk")],
            "timers": [span(row) for row in session.timers.order_by("pk")]}


def correction_instant(value, zone, field):
    try:
        return local_instant(value, zone)
    except Problem as error:
        reason = {
            "attendance_ambiguous_time": "ambiguous_time",
            "attendance_invalid_local_time": "invalid_local_time",
        }.get(error.code, "invalid_date")
        raise Problem(error.code, error.status, field=field, reason=reason) from None


def proposed_snapshot(original, data, zone):
    if data.keys() - {"started_at", "ended_at", "breaks", "reason"}:
        raise Problem("invalid_attendance_correction")
    result = {**original, "breaks": [dict(row) for row in original["breaks"]]}
    for key in ("started_at", "ended_at"):
        if key in data:
            result[key] = correction_instant(data[key], zone, key)
    changes = data.get("breaks", [])
    if not isinstance(changes, list) or len(changes) > len(result["breaks"]):
        raise Problem("invalid_attendance_correction")
    by_id = {row["id"]: row for row in result["breaks"]}
    for index, change in enumerate(changes):
        if not isinstance(change, dict) or change.keys() - {"id", "started_at", "ended_at"}:
            raise Problem("invalid_attendance_correction")
        row = by_id.pop(identifier(change.get("id"), "break"), None)
        if row is None:
            raise Problem("invalid_attendance_correction")
        for key in ("started_at", "ended_at"):
            if key in change:
                row[key] = correction_instant(change[key], zone, f"breaks.{index}.{key}")
    if result == original:
        raise Problem("invalid_attendance_correction")
    return result


def interval(row):
    return datetime.fromisoformat(row["started_at"]), datetime.fromisoformat(row["ended_at"]) if row["ended_at"] else None


def correction_bounds(original, proposed):
    old_start, old_end = interval(original)
    start, end = interval(proposed)
    return min(old_start, start), max(old_end, end) if old_end else None


def validate_correction(session, proposed, now):
    start, end = interval(proposed)
    if not end or not start < end <= now or end - start > timedelta(days=31):
        raise Problem("invalid_attendance_interval", 409, field="ended_at", reason="invalid_shift_end")
    employee = session.employee
    zone = ZoneInfo(session.timezone)
    first_day = start.astimezone(zone).date()
    last_day = (end - timedelta(microseconds=1)).astimezone(zone).date()
    if first_day < employee.joined or employee.ended and last_day > employee.ended:
        raise Problem("attendance_employee_inactive", 409)
    if AttendanceSession.objects.filter(employee=employee, started_at__lt=end).exclude(pk=session.pk).filter(
        Q(ended_at__isnull=True) | Q(ended_at__gt=start)
    ).exists():
        raise Problem("attendance_shift_overlap", 409)
    pairs = []
    for pause in proposed["breaks"]:
        a, b = interval(pause)
        if b is None or not start <= a <= b <= end:
            raise Problem("attendance_corrected_break", 409)
        pairs.append((a, b))
    ordered = sorted(pairs)
    if any(left[1] > right[0] for left, right in zip(ordered, ordered[1:])):
        raise Problem("attendance_corrected_break", 409)
    for timer in proposed["timers"]:
        a, b = interval(timer)
        if b is None or not start <= a <= b <= end or any(a < y and b > x for x, y in pairs):
            raise Problem("attendance_corrected_timer", 409)


def overlaps(rows, start, end, from_field, until_field):
    if end is not None:
        rows = rows.filter(**{from_field + "__lt": end})
    return rows.filter(Q(**{until_field + "__gt": start}) | Q(**{until_field + "__isnull": True}))


def pending_for_week(sheet):
    rows = AttendanceCorrection.objects.filter(session__employee_id=sheet.employee_id, state="pending")
    return overlaps(rows, sheet.starts_at, sheet.ends_at, "affects_from", "affects_until").exists()


def draft_weeks_only(session, start, end):
    rows = WeeklyTimesheet.objects.filter(employee_id=session.employee_id, site_id=session.site_id).exclude(state="draft")
    if overlaps(rows, start, end, "starts_at", "ends_at").exists():
        raise Problem("attendance_correction_locked_weeks", 409)


def exact_net(row):
    start, end = interval(row)
    if end is None or any(not pause["ended_at"] for pause in row["breaks"]):
        return None
    return seconds(start, end) - sum(seconds(*interval(pause)) for pause in row["breaks"])


def correction_data(row, user):
    own = user.pk in (row.session.employee.user_id, row.requested_by_id)
    old, new = exact_net(row.original), exact_net(row.proposed)
    return {"id": row.pk, "session_id": row.session_id, "site_id": row.site_id,
            "employee": row.session.employee.name, "timezone": row.session.timezone,
            "state": row.state, "revision": row.revision, "reason": row.reason, "review_note": row.review_note,
            "original": row.original, "proposed": row.proposed, "original_net_seconds": old,
            "proposed_net_seconds": new, "net_delta_seconds": new - old if old is not None and new is not None else None,
            "can_review": row.state == "pending" and not own and has_permission(user, "attendance.correct", row.site_id)}


def visible_corrections(user):
    return own_or_scoped(AttendanceCorrection.objects.select_related("session__employee"), user,
                         "attendance.correct", "attendance.record", "session__employee__user_id")


def pending_corrections(user, site=None):
    rows = allowed_rows(AttendanceCorrection.objects.filter(state="pending"), user, "attendance.correct")
    rows = rows.exclude(Q(session__employee__user=user) | Q(requested_by=user))
    return rows if site is None else rows.filter(site_id=site)


@never_cache
@endpoint("GET", module="hr")
def corrections(request):
    if "page_size" in request.GET:
        from backbone.services.pagination import operational_page
        return operational_page(request,visible_corrections(request.user).order_by('-pk'),lambda r:correction_data(r,request.user),['session__employee__name','reason','state'])
    rows = visible_corrections(request.user).order_by("-pk")[:100]
    return {"items": [correction_data(row, request.user) for row in rows]}


@never_cache
@endpoint("GET", module="hr")
def correction(request, pk):
    return correction_data(one(visible_corrections(request.user), pk), request.user)


@never_cache
@endpoint("GET", "POST", module="hr")
def request_correction(request, pk):
    employee = my_employee(request.user)
    if request.method == "GET":
        session = one(AttendanceSession.objects.filter(employee=employee), pk)
        return {**session_snapshot(session), "timezone": session.timezone}
    data = body(request)

    def create():
        current = lock_employee(employee)
        session = one(AttendanceSession.objects.filter(employee=current), pk)
        if session.corrections.filter(state="pending").exists():
            raise Problem("attendance_correction_pending", 409)
        original = session_snapshot(session)
        proposed = proposed_snapshot(original, data, ZoneInfo(session.timezone))
        reason = string(data, "reason", 500)
        now = event_time()
        validate_correction(session, proposed, now)
        start, end = correction_bounds(original, proposed)
        draft_weeks_only(session, start, end)
        row = AttendanceCorrection.objects.create(session=session, site_id=session.site_id, requested_by=request.user,
            reason=reason, original=original, proposed=proposed, affects_from=start, affects_until=end)
        audit(request.user, "attendance.correction.request", row.pk)
        return correction_data(row, request.user)

    return once(request, "attendance.correction.request", {"session_id": pk, "data": data}, create)


@never_cache
@endpoint("POST", module="hr")
def decide_correction(request, pk):
    user = request.user
    data = body(request)
    with transaction.atomic():
        initial = one(visible_corrections(user), pk)
        require(user, "attendance.correct", initial.site_id)
        employee = Employee.objects.select_for_update().get(pk=initial.session.employee_id)
        row = AttendanceCorrection.objects.select_for_update().get(pk=pk)
        if user.pk in (employee.user_id, row.requested_by_id):
            raise Problem("hr_separate_reviewer", 403)
        if identifier(data.get("revision"), "revision") != row.revision or row.state != "pending":
            raise Problem("revision_conflict", 409)
        choice = data.get("action")
        if choice not in ("approve", "reject"):
            raise Problem("invalid_attendance_correction")
        row.review_note = string(data, "reason", 500, required=choice == "reject")
        now = event_time()
        if choice == "approve":
            session = row.session
            if session_snapshot(session) != row.original:
                raise Problem("attendance_correction_stale", 409)
            validate_correction(session, row.proposed, now)
            draft_weeks_only(session, row.affects_from, row.affects_until)
            session.started_at, session.ended_at = interval(row.proposed)
            session.save(update_fields=["started_at", "ended_at"])
            by_id = {item["id"]: item for item in row.proposed["breaks"]}
            for pause in session.breaks.all():
                pause.started_at, pause.ended_at = interval(by_id[pause.pk])
                pause.save(update_fields=["started_at", "ended_at"])
            point = session.started_at
            settings = policy(session.site_id)
            while point < session.ended_at:
                point = ensure_week(employee, point, session.timezone, settings).ends_at
            row.state = "approved"
        else:
            row.state = "rejected"
        row.revision += 1
        row.reviewed_at, row.reviewed_by = now, user
        row.save(update_fields=["state", "revision", "reviewed_at", "reviewed_by", "review_note"])
        audit(user, "attendance.correction." + choice, row.pk)
        return correction_data(row, user)


def overtime_requests(payload, projection):
    if not isinstance(payload, dict) or len(payload) > len(projection["days"]):
        raise Problem("invalid_attendance_overtime")
    limits = {row["day"]: row["eligible_overtime_seconds"] // 60 for row in projection["days"]}
    requests = {}
    for day, item in payload.items():
        if day not in limits or not isinstance(item, dict):
            raise Problem("invalid_attendance_overtime")
        minutes = item.get("minutes")
        if type(minutes) is not int or not 1 <= minutes <= limits[day]:
            raise Problem("invalid_attendance_overtime")
        requests[day] = {"minutes": minutes, "reason": string(item, "reason", 500)}
    return requests


def overtime_decisions(payload, requests):
    if not isinstance(payload, dict) or payload.keys() - requests.keys():
        raise Problem("invalid_attendance_overtime")
    result = {}
    for day, request in requests.items():
        item = payload.get(day, {"minutes": request["minutes"]})
        if not isinstance(item, dict):
            raise Problem("invalid_attendance_overtime")
        minutes = item.get("minutes")
        if type(minutes) is not int or not 0 <= minutes <= request["minutes"]:
            raise Problem("invalid_attendance_overtime")
        result[day] = {"minutes": minutes, "reason": string(item, "reason", 500, required=minutes < request["minutes"])}
    return result
