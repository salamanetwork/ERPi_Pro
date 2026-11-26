from datetime import date, timedelta
import re
from unicodedata import normalize
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.views.decorators.cache import never_cache

from accounting.views import day_field
from backbone.access import audit, has_permission, visible
from backbone.http import Problem, body, endpoint, identifier, once, require, string
from backbone.registry import enabled_modules
from backbone.services.pagination import directory_rows, paginate, positive_filter, search_rows
from .access import allowed_rows, one, own_or_scoped
from .models import AttendanceSession, Employee, EmployeeNumbering, HRReference, Offboarding

REFERENCES = {"departments": "department", "job-titles": "title", "grades": "grade"}
AUTOMATIC_PREFIXES = {"grades": "GRD", "job-titles": "JOB"}


def visible_employees(user):
    rows = own_or_scoped(Employee.objects.all(), user, "hr.view", "hr.self")
    for permission in ("hr.manage", "hr.identity.view_full", "hr.leave.request", "hr.offboarding.request", "hr.offboarding.approve"):
        rows |= allowed_rows(Employee.objects.all(), user, permission)
    if "payroll" in enabled_modules():
        rows |= allowed_rows(Employee.objects.all(), user, "payroll.prepare")
    return rows.select_related("department_ref", "title_ref", "grade_ref").order_by("number")


def employee_rows(user, params):
    rows = search_rows(visible_employees(user), params, ("number", "name"), number_fields=("number",))
    for key, field in (("site", "site_id"), ("department", "department_ref_id")):
        if value := positive_filter(params, key):
            rows = rows.filter(**{field: value})
    state = params.get("status", "")
    if state not in ("", "active", "ended", "archived"):
        raise Problem("invalid_status")
    rows = rows.filter(archived_at__isnull=state != "archived")
    if state in ("active", "ended"):
        from inventory.models import SiteSettings

        now = timezone.now()
        configured = list(SiteSettings.objects.values_list("site_id", "timezone"))
        ended = Q(~Q(site_id__in=[site for site, _ in configured]), ended__lt=now.date())
        for site, zone in configured:
            ended |= Q(site_id=site, ended__lt=now.astimezone(ZoneInfo(zone)).date())
        rows = rows.filter(ended) if state == "ended" else rows.exclude(ended)
    return rows


def reference_choices():
    choices = {key: [] for key in REFERENCES}
    for row in HRReference.objects.filter(active=True, is_deleted=False, kind__in=REFERENCES).order_by("name", "pk"):
        choices[row.kind].append(reference_data(row))
    return choices


def employment_references(data, fields, current=None):
    for kind, field in REFERENCES.items():
        parameter = field + "_ref_id"
        if parameter not in data:
            if current:
                fields.pop(field, None)
            elif field != "grade" and fields.get(field):
                # Old API clients can submit the pre-1.8.3 textual fields.
                reference = HRReference.objects.filter(kind=kind, name__iexact=fields[field], active=True).first()
                if reference:
                    fields[parameter] = reference.pk
            continue
        key = identifier(data[parameter], field, optional=field == "grade")
        rows = HRReference.objects.select_for_update().filter(kind=kind, is_deleted=False)
        if current:
            rows = rows.filter(Q(active=True) | Q(pk=getattr(current, parameter)))
        else:
            rows = rows.filter(active=True)
        reference = rows.filter(pk=key).first() if key is not None else None
        if key is not None and reference is None:
            raise Problem("invalid_" + field, field=parameter, reason="invalid_choice")
        fields[parameter] = key
        if field != "grade":
            fields[field] = reference.name


def numbering_row():
    return EmployeeNumbering.objects.get_or_create(pk=1)[0]


def create_employee(data, fields):
    numbering = EmployeeNumbering.objects.select_for_update().get(pk=numbering_row().pk)
    if numbering.automatic:
        number = numbering.next_number
        while number < 2**63 - 1 and Employee.objects.filter(number=str(number)).exists():
            number += 1
        if number >= 2**63 - 1:
            raise Problem("hr_sequence_exhausted", 409)
        numbering.next_number = number + 1
        numbering.save(update_fields=["next_number"])
        code = str(number)
    else:
        code = string(data, "number", 40).upper()
    row, created = Employee.objects.get_or_create(number=code, defaults=fields)
    if not created:
        raise Problem("hr_employee_number_exists", 409)
    return row


@never_cache
@endpoint("GET", module="hr")
def employee_options(request):
    require(request.user, "hr.manage")
    return {"references": reference_choices(), "automatic": numbering_row().automatic}


@never_cache
@endpoint("GET", "PATCH", admin=True, module="hr")
def employee_numbering(request):
    with transaction.atomic():
        row = EmployeeNumbering.objects.select_for_update().get(pk=numbering_row().pk)
        if request.method == "PATCH":
            data = body(request)
            automatic = data.get("automatic")
            if type(automatic) is not bool:
                raise Problem("invalid_numbering")
            if "next_number" in data:
                number = identifier(data["next_number"], "next_number")
                if number < row.next_number:
                    raise Problem("hr_sequence_cannot_decrease", 409)
                row.next_number = number
            row.automatic = automatic
            row.save(update_fields=["automatic", "next_number"])
            audit(request.user, "hr.employee.numbering", "")
        return {"automatic": row.automatic, "next_number": str(row.next_number)}


def reference_data(row):
    return {"id": row.pk, "code": row.code, "name": row.name, "active": row.active, "revision": row.revision}


def reference_prefix(name, kind):
    letters = re.sub(r"[^A-Z]", "", normalize("NFKD", name).upper())
    return "MGR" if letters == "MANAGER" else letters[:3] or AUTOMATIC_PREFIXES[kind]


def next_reference_suffix(rows, prefix):
    codes = rows.filter(code__startswith=prefix).values_list("code", flat=True)
    suffixes = (code[len(prefix):] for code in codes.iterator())
    return max((int(suffix) for suffix in suffixes if re.fullmatch(r"[0-9]{3,9}", suffix)), default=0) + 1


def reference_code(prefix, suffix):
    if suffix >= 10**9:
        raise Problem("hr_reference_codes_exhausted", 409)
    return f"{prefix}{suffix:03d}"


@never_cache
@endpoint("GET", module="hr")
def preview_reference_code(request, kind):
    require(request.user, "hr.manage", None)
    if kind not in AUTOMATIC_PREFIXES:
        raise Problem("not_found", 404)
    prefix = reference_prefix(string(request.GET, "name", 120), kind)
    rows = HRReference.objects.filter(kind=kind)
    return {"code": reference_code(prefix, next_reference_suffix(rows, prefix))}


@never_cache
@endpoint("GET", "POST", module="hr")
def references(request, kind):
    if kind not in REFERENCES:
        raise Problem("not_found", 404)
    rows = HRReference.objects.filter(kind=kind, is_deleted=False).order_by("code")
    if request.method == "GET":
        require(request.user, "hr.manage")
        return paginate(request, directory_rows(rows, request.GET), reference_data)
    require(request.user, "hr.manage", None)
    data = body(request)
    automatic = data.get("auto_code", False)
    active = data.get("active", True)
    if type(automatic) is not bool or (automatic and kind not in AUTOMATIC_PREFIXES):
        raise Problem("invalid_auto_code")
    if type(active) is not bool:
        raise Problem("invalid_active")
    code = string(data, "code", 40, required=not automatic).upper()
    if not automatic and not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]*", code):
        raise Problem("invalid_code")
    name = string(data, "name", 120)
    defaults = {"name": name, "active": active}

    def create():
        if automatic:
            prefix = reference_prefix(name, kind)
            suffix = next_reference_suffix(rows, prefix)
            while True:
                candidate = reference_code(prefix, suffix)
                row, created = HRReference.objects.get_or_create(kind=kind, code=candidate, defaults=defaults)
                if created:
                    break
                # A concurrent create can win the unique key after the preview.
                suffix += 1
        else:
            row, created = HRReference.objects.get_or_create(kind=kind, code=code, defaults=defaults)
            if not created:
                raise Problem("hr_reference_code_exists", 409)
        audit(request.user, "hr.reference.create", f"{kind}/{row.code}")
        return reference_data(row)

    if request.headers.get("Idempotency-Key"):
        return once(request, "hr.reference.create", {**data, "kind": kind}, create)
    with transaction.atomic():
        return create(), 201


@never_cache
@endpoint("GET", "PATCH", "DELETE", module="hr")
def reference(request, kind, pk):
    require(request.user, "hr.manage", None if request.method in ("PATCH", "DELETE") else ...)
    rows = HRReference.objects.filter(kind=kind, is_deleted=False)
    if request.method == "DELETE":
        from backbone.deletion import perform_record_deletion
        row = one(rows, pk)
        return perform_record_deletion(
            row,
            request.user,
            f"hr.reference.{kind}.delete",
            has_dependencies_fn=lambda ref: Employee.objects.filter(
                Q(department_ref=ref) | Q(title_ref=ref) | Q(grade_ref=ref)
            ).exists(),
        )
    if request.method == "GET":
        return reference_data(one(rows, pk))
    data = body(request)
    with transaction.atomic():
        row = one(rows.select_for_update(), pk)
        if identifier(data.get("revision"), "revision") != row.revision:
            raise Problem("revision_conflict", 409)
        if type(data.get("active")) is not bool:
            raise Problem("invalid_active")
        row.name = string(data, "name", 120)
        row.active = data["active"]
        row.revision += 1
        row.save(update_fields=["name", "active", "revision"])
        audit(request.user, "hr.reference.update", f"{kind}/{row.code}")
        return reference_data(row)


@never_cache
@endpoint("GET", module="hr")
def export_employees(request):
    from backbone.services.csv_export import csv_response

    require(request.user, "platform.export")
    rows = visible(employee_rows(request.user, request.GET), request.user, "platform.export")
    return csv_response("employees", ("number", "name", "site", "department", "job_title", "grade", "hire_date", "archived"),
                        ((row.number, row.name, row.site.name, row.department_ref.name if row.department_ref else row.department,
                          row.title_ref.name if row.title_ref else row.title, row.grade_ref.name if row.grade_ref else "",
                          row.joined, bool(row.archived_at)) for row in rows.select_related("site").iterator()))


@never_cache
@endpoint("POST", module="hr")
def archive_employee(request, pk):
    data = body(request)
    with transaction.atomic():
        row = one(allowed_rows(Employee.objects.select_for_update(), request.user, "hr.manage"), pk)
        if identifier(data.get("revision"), "revision") != row.revision:
            raise Problem("revision_conflict", 409)
        if type(data.get("active")) is not bool:
            raise Problem("invalid_active")
        row.archived_at = None if data["active"] else timezone.now()
        row.revision += 1
        row.save(update_fields=["archived_at", "revision"])
        audit(request.user, "hr.employee.restore" if data["active"] else "hr.employee.archive", row.pk)
        return {"archived": bool(row.archived_at), "revision": row.revision}


def offboarding_data(row, user):
    return {"id": row.pk, "employee_id": row.employee_id, "employee": row.employee.name, "last_day": row.last_day.isoformat(),
            "kind": row.kind, "reason": row.reason, "note": row.note, "state": row.state, "revision": row.revision,
            "can_approve": row.state == "submitted" and user.pk not in (row.requested_by_id, row.employee.user_id)
            and has_permission(user, "hr.offboarding.approve", row.employee.site_id)}


@never_cache
@endpoint("GET", "POST", module="hr")
def offboardings(request):
    user = request.user
    if request.method == "GET":
        records = Offboarding.objects.select_related("employee").order_by("-pk")
        rows = allowed_rows(records, user, "hr.offboarding.request", "employee__site_id")
        rows |= allowed_rows(records, user, "hr.offboarding.approve", "employee__site_id")
        if employee := positive_filter(request.GET, "employee"):
            rows = rows.filter(employee_id=employee)
        return paginate(request, rows, lambda row: offboarding_data(row, user))
    data = body(request)
    person = identifier(data.get("employee_id"), "employee")
    kind = data.get("kind")
    if kind not in ("resignation", "termination"):
        raise Problem("invalid_kind")
    last_day = day_field(data, "last_day")
    reason = string(data, "reason", 500)

    def submit():
        employee = one(allowed_rows(Employee.objects.select_for_update(), user, "hr.offboarding.request"), person)
        if employee.ended or employee.archived_at or not employee.joined <= last_day < date.max:
            raise Problem("hr_invalid_dates", 409)
        if Offboarding.objects.filter(employee=employee, state__in=("submitted", "approved")).exists():
            raise Problem("hr_offboarding_exists", 409)
        row = Offboarding.objects.create(employee=employee, kind=kind, last_day=last_day,
                                        reason=reason, requested_by=user)
        audit(user, "hr.offboarding.submit", f"{employee.number} / {row.pk} / {last_day}")
        return offboarding_data(row, user)

    return once(request, "hr.offboarding.submit", data, submit)


@never_cache
@endpoint("POST", module="hr")
def decide_offboarding(request, pk):
    user = request.user
    data = body(request)
    action = data.get("action")
    if action not in ("approve", "reject"):
        raise Problem("invalid_action")
    note = string(data, "note", 500, required=action == "reject")

    def decide():
        initial = one(allowed_rows(Offboarding.objects.all(), user, "hr.offboarding.approve", "employee__site_id"), pk)
        employee = Employee.objects.select_for_update().get(pk=initial.employee_id)
        row = Offboarding.objects.select_for_update().get(pk=pk)
        if user.pk in (row.requested_by_id, employee.user_id):
            raise Problem("hr_separate_reviewer", 403)
        if row.revision != identifier(data.get("revision"), "revision") or row.state != "submitted":
            raise Problem("revision_conflict", 409)
        if action == "approve":
            from backbone.site_admin import require_active_site
            from .attendance import midnight, site_timezone
            from .models import WeeklyTimesheet

            require_active_site(employee.site_id, lock=True)
            if employee.ended or employee.archived_at or not employee.joined <= row.last_day < date.max:
                raise Problem("hr_invalid_dates", 409)
            boundary = midnight(row.last_day + timedelta(days=1), ZoneInfo(site_timezone(employee.site_id)))
            if AttendanceSession.objects.filter(employee=employee, started_at__gte=boundary).exists():
                raise Problem("hr_offboarding_attendance_conflict", 409)
            if WeeklyTimesheet.objects.filter(employee=employee, starts_at__gte=boundary).exclude(state="draft").exists():
                raise Problem("attendance_week_locked", 409)
            employee.ended = row.last_day
            employee.revision += 1
            employee.save(update_fields=["ended", "revision"])
        row.state = "approved" if action == "approve" else "rejected"
        row.revision += 1
        row.decided_at = timezone.now()
        row.decided_by = user
        row.note = note
        row.save(update_fields=["state", "revision", "decided_at", "decided_by", "note"])
        audit(user, "hr.offboarding." + action, f"{employee.number} / {row.pk} / {row.last_day}")
        return offboarding_data(row, user)

    return once(request, "hr.offboarding.decide", {"offboarding": pk, **data}, decide)
