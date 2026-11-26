
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q, Sum, Value
from django.db.models.functions import Coalesce, Concat
from django.utils import timezone
from django.views.decorators.cache import never_cache

from accounting.centers import center_choices, center_for_site
from accounting.views import day_field
from backbone.access import audit, has_permission
from backbone.http import Problem, body, endpoint, identifier, once, require, site, string
from purchasing.money import decimal_value
from .access import allowed_rows, authorize, one, own_or_scoped
from backbone.services.pagination import paginate
from backbone.validation import Validation
from .directory import create_employee, employee_rows, employment_references, visible_employees
from .models import Compensation, Employee, HRReference, Leave, LeaveAdjustment, Payslip


def employee_data(row):
    return {"id": row.pk, "number": row.number, "name": row.name, "site_id": row.site_id,
            "department": row.department_ref.name if row.department_ref else row.department,
            "title": row.title_ref.name if row.title_ref else row.title, "joined": row.joined.isoformat(),
            "department_ref_id": row.department_ref_id, "title_ref_id": row.title_ref_id, "grade_ref_id": row.grade_ref_id,
            "grade": row.grade_ref.name if row.grade_ref else "", "archived": bool(row.archived_at),
            "ended": row.ended.isoformat() if row.ended else None, "user_id": row.user_id, "revision": row.revision}


def employment(data):
    checks = Validation()
    site_id = checks.read("site_id", site, data)
    if site_id is None and "site_id" not in checks.fields:
        checks.add("site_id", "invalid_site", ["required"])
    start = checks.read("joined", day_field, data, "joined")
    name = checks.read("name", string, data, "name", 150)
    department = checks.read("department_ref_id", string, data, "department", 120,
                             required="department_ref_id" not in data and "department" in data)
    title = checks.read("title_ref_id", string, data, "title", 120,
                        required="title_ref_id" not in data and "title" in data)
    user_id = checks.read("user_id", identifier, data.get("user_id"), "user", optional=True)
    for key in ("department_ref_id", "title_ref_id", "grade_ref_id"):
        if key in data:
            checks.read(key, identifier, data[key], key.removesuffix("_ref_id"), optional=key == "grade_ref_id")
    checks.finish()
    return {"site_id": site_id, "name": name, "department": department, "title": title,
            "joined": start, "user_id": user_id}


def check_link(fields, pk=None):
    user_id = fields["user_id"]
    if user_id:
        if not get_user_model().objects.select_for_update().filter(pk=user_id, is_active=True).exists():
            raise Problem("invalid_user")
        if Employee.objects.filter(user_id=user_id).exclude(pk=pk).exists():
            raise Problem("hr_user_already_linked", 409)


@never_cache
@endpoint("GET", module="hr")
def linkable_users(request, site_id):
    user = request.user
    require(user, "hr.manage", site_id)
    require(user, "hr.identity.link", site_id)
    from backbone.models import Assignment, Site

    one(Site.objects.all(), site_id)
    employees = Employee.objects.filter(site_id=site_id)
    employee_id = request.GET.get("employee", "")
    employee = None
    if employee_id:
        if not employee_id.isascii() or not employee_id.isdecimal() or len(employee_id) > 18:
            raise Problem("invalid_employee")
        employee = one(employees.select_related("user", "user__profile"), int(employee_id))

    def account(row):
        middle = getattr(getattr(row, "profile", None), "middle_name", "")
        return {"id": row.pk, "username": row.get_username(), "name": " ".join(
            part for part in (row.first_name, middle, row.last_name) if part)}

    selected = account(employee.user) if employee and employee.user_id else None
    locked = bool(employee and Payslip.objects.filter(employee=employee).exists())
    term = string(request.GET, "q", 120, required=False)
    if locked or len(term) < 2:
        return {"selected": selected, "locked": locked, "items": []}
    users = get_user_model().objects.filter(is_active=True).select_related("profile")
    if not user.is_superuser:
        in_scope = Assignment.objects.filter(Q(site_id=site_id) | Q(site_id__isnull=True)).values("user_id")
        users = users.filter(pk__in=in_scope)
    linked = Employee.objects.exclude(user_id__isnull=True)
    if employee:
        linked = linked.exclude(pk=employee.pk)
    users = users.exclude(pk__in=linked.values("user_id")).annotate(
        full_name=Concat("first_name", Value(" "), Coalesce("profile__middle_name", Value("")),
                         Value(" "), "last_name"),
        short_name=Concat("first_name", Value(" "), "last_name"),
    )
    users = users.filter(Q(username__icontains=term) | Q(full_name__icontains=term) | Q(short_name__icontains=term))
    return {"selected": selected, "locked": False,
            "items": [account(row) for row in users.order_by("username")[:20]]}


@never_cache
@endpoint("GET", "POST", module="hr")
def employees(request):
    user = request.user
    if request.method == "GET":
        if "page" in request.GET:
            rows = employee_rows(user, request.GET)
            departments = visible_employees(user).order_by().values("department_ref_id")
            choices = HRReference.objects.filter(pk__in=departments).order_by("name", "pk")
            return {**paginate(request, rows, lambda row: {**employee_data(row), "own": row.user_id == user.pk}),
                    "departments": list(choices.values("id", "name"))}
        rows = visible_employees(user).filter(archived_at__isnull=True)
        return {"items": [{**employee_data(row), "own": row.user_id == user.pk} for row in rows]}
    data = body(request)
    fields = employment(data)
    require(user, "hr.manage", fields["site_id"])
    if data.get("ended"):
        raise Problem("hr_use_offboarding", 409)
    if not fields["department"] and "department_ref_id" not in data:
        raise Problem("invalid_department", field="department_ref_id", reason="required")
    if not fields["title"] and "title_ref_id" not in data:
        raise Problem("invalid_title", field="title_ref_id", reason="required")
    if fields["user_id"] is not None:
        require(user, "hr.identity.link", fields["site_id"])

    def create():
        from backbone.site_admin import require_active_site

        require_active_site(fields["site_id"], lock=True)
        check_link(fields)
        employment_references(data, fields)
        row = create_employee(data, fields)
        audit(user, "hr.employee.create", row.pk)
        return employee_data(row)

    return once(request, "hr.employee.create", data, create)


@never_cache
@endpoint("GET", "PATCH", module="hr")
def employee(request, pk):
    user = request.user
    if request.method == "GET":
        row = one(visible_employees(user), pk)
        return {**employee_data(row), "own": row.user_id == user.pk}
    data = body(request)
    with transaction.atomic():
        row = one(allowed_rows(Employee.objects.select_for_update(), user, "hr.manage"), pk)
        if identifier(data.get("revision"), "revision") != row.revision:
            raise Problem("revision_conflict", 409)
        fields = employment(data)
        if "ended" in data and (data["ended"] or None) != (row.ended.isoformat() if row.ended else None):
            raise Problem("hr_use_offboarding", 409)
        if row.ended and row.ended < fields["joined"]:
            raise Problem("hr_invalid_dates")
        employment_references(data, fields, row)
        if row.birth_date and fields["joined"] < row.birth_date:
            raise Problem("invalid_birth_date")
        if fields["site_id"] != row.site_id:
            raise Problem("hr_employee_site_fixed", 409)
        if "user_id" not in data:
            fields["user_id"] = row.user_id
        if fields["user_id"] != row.user_id:
            require(user, "hr.identity.link", row.site_id)
            if Payslip.objects.filter(employee=row).exists():
                raise Problem("hr_employee_link_fixed", 409)
            check_link(fields, row.pk)
        for key, value in fields.items():
            setattr(row, key, value)
        row.revision += 1
        row.save()
        audit(user, "hr.employee.edit", pk)
        return employee_data(row)


def term_data(row):
    return {"id": row.pk, "effective": row.effective.isoformat(), "center_id": row.center_id,
            "basic": str(row.basic), "allowance": str(row.allowance)}


@never_cache
@endpoint("GET", "POST", module="payroll")
def compensation(request, pk):
    user = request.user
    row = one(allowed_rows(Employee.objects.all(), user, "payroll.prepare"), pk)
    if request.method == "GET":
        return {"items": [term_data(term) for term in row.terms.order_by("-effective")],
                "centers": center_choices(row.site_id)}
    data = body(request)
    day = day_field(data, "effective")
    basic = decimal_value(data.get("basic"), 14, 2, "basic")
    allowance = decimal_value(data.get("allowance"), 14, 2, "allowance")
    require(user, "payroll.prepare", row.site_id)

    def create():
        current = Employee.objects.select_for_update().get(pk=row.pk)
        if day < current.joined or current.ended and day > current.ended:
            raise Problem("hr_invalid_dates", field="effective", reason="employment_date")
        center = center_for_site(data.get("center_id"), current.site_id, required=True)
        record, fresh = Compensation.objects.get_or_create(employee=current, effective=day,
            defaults={"basic": basic, "allowance": allowance, "center": center, "actor": user})
        if not fresh:
            raise Problem("hr_compensation_exists", 409)
        audit(user, "hr.compensation.add", f"{pk} / {day}")
        return term_data(record)

    return once(request, "hr.compensation", {"employee": pk, **data}, create)


def leave_days(start, end):
    return (end - start).days + 1


def balance(row, day):
    added = LeaveAdjustment.objects.filter(employee=row, day__lte=day).aggregate(amount=Sum("days"))["amount"] or 0
    used = sum(leave_days(item.start, item.end) for item in Leave.objects.filter(employee=row, paid=True, state="approved"))
    return added - used


def leave_data(row, user):
    return {"id": row.pk, "employee_id": row.employee_id, "employee": row.employee.name, "site_id": row.site_id,
            "start": row.start.isoformat(), "end": row.end.isoformat(), "paid": row.paid,
            "state": row.state, "reason": row.reason, "decision": row.decision,
            "can_approve": user.pk not in (row.requested_by_id, row.employee.user_id)
            and has_permission(user, "hr.leave.approve", row.site_id)}


@never_cache
@endpoint("GET", "POST", module="hr")
def leaves(request):
    user = request.user
    if request.method == "GET":
        visible = own_or_scoped(Leave.objects.select_related("employee"), user, "hr.leave.approve", "hr.self", "employee__user_id")
        records = allowed_rows(Leave.objects.select_related("employee"), user, "hr.view")
        if "page_size" in request.GET:
            from backbone.services.pagination import operational_page
            return operational_page(request,(visible|records).order_by("-pk"),lambda row:leave_data(row,user),['employee__name', 'state', 'reason'])
        return {"items": [leave_data(row, user) for row in (visible | records).order_by("-pk")]}
    data = body(request)
    row = one(Employee.objects.all(), identifier(data.get("employee_id"), "employee"))
    authorize(user, row, "hr.leave.request", "hr.self")
    start, end = day_field(data, "start"), day_field(data, "end")
    paid = data.get("paid")
    if end < start:
        raise Problem("hr_invalid_dates", field="end", reason="date_order")
    if leave_days(start, end) > 366 or type(paid) is not bool:
        raise Problem("hr_invalid_dates")

    def create():
        from backbone.site_admin import require_active_site

        require_active_site(row.site_id, lock=True)
        current = Employee.objects.select_for_update().get(pk=row.pk)
        if start < current.joined or current.ended and end > current.ended:
            raise Problem("hr_invalid_dates", field="start" if start < current.joined else "end", reason="employment_date")
        if Leave.objects.filter(employee=current, start__lte=end, end__gte=start, state__in=("pending", "approved")).exists():
            raise Problem("hr_leave_overlap", 409)
        item = Leave.objects.create(employee=current, site_id=current.site_id, start=start, end=end, paid=paid,
                                    reason=string(data, "reason", 500), requested_by=user)
        audit(user, "hr.leave.request", item.pk)
        return leave_data(item, user)

    return once(request, "hr.leave.create", data, create)


@never_cache
@endpoint("POST", module="hr")
def decide_leave(request, pk):
    user = request.user
    data = body(request)
    initial = one(allowed_rows(Leave.objects.all(), user, "hr.leave.approve"), pk)
    def decide():
        Employee.objects.select_for_update().get(pk=initial.employee_id)
        row = Leave.objects.select_for_update().get(pk=pk)
        if user.pk in (row.requested_by_id, row.employee.user_id):
            raise Problem("hr_separate_reviewer", 403)
        action = data.get("action")
        if row.state != "pending" or action not in ("approve", "reject"):
            raise Problem("hr_invalid_state", 409)
        if action == "approve" and row.paid and balance(row.employee, row.start) < leave_days(row.start, row.end):
            raise Problem("hr_insufficient_leave", 409)
        if action == "approve":
            from backbone.site_admin import require_active_site
            from .attendance import ensure_leave_can_be_approved
            require_active_site(row.site_id, lock=True)
            if row.employee.archived_at or row.start < row.employee.joined or (row.employee.ended and row.end > row.employee.ended):
                raise Problem("hr_invalid_dates", 409)
            ensure_leave_can_be_approved(row)
        row.state = "approved" if action == "approve" else "rejected"
        row.decision = string(data, "reason", 500, required=action == "reject")
        row.save(update_fields=["state", "decision"])
        audit(user, "hr.leave." + action, pk)
        return leave_data(row, user)

    return once(request, "hr.leave.decide", {"leave": pk, **data}, decide)


@never_cache
@endpoint("GET", "POST", module="hr")
def leave_balance(request, pk):
    user = request.user
    row = one(Employee.objects.all(), pk)
    if request.method == "GET":
        if not has_permission(user, "hr.manage", row.site_id):
            authorize(user, row, "hr.view", "hr.self")
        return {"available": balance(row, timezone.localdate())}
    require(user, "hr.manage", row.site_id)
    data = body(request)
    day = day_field(data, "day")
    days = data.get("days")
    if type(days) is not int or days == 0 or abs(days) > 366:
        raise Problem("hr_invalid_leave_days")

    def create():
        current = Employee.objects.select_for_update().get(pk=row.pk)
        adjustment = LeaveAdjustment.objects.create(employee=current, day=day, days=days, reason=string(data, "reason", 500))
        audit(user, "hr.leave.adjust", f"{pk} / {adjustment.pk}")
        return {"id": adjustment.pk}

    return once(request, "hr.leave.adjust", {"employee": pk, **data}, create)
