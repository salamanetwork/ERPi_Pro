from django.conf import settings
from django.db import models

PROTECT = models.PROTECT


class HRReference(models.Model):
    kind = models.CharField(max_length=16)
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=120)
    active = models.BooleanField(default=True)
    revision = models.PositiveIntegerField(default=1)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["kind", "code"], name="hr_reference_kind_code")]


class EmployeeNumbering(models.Model):
    automatic = models.BooleanField(default=True)
    next_number = models.PositiveBigIntegerField(default=10001)


class Employee(models.Model):
    number = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=150)
    site = models.ForeignKey("backbone.Site", on_delete=PROTECT)
    department = models.CharField(max_length=120)
    title = models.CharField(max_length=120)
    joined = models.DateField()
    ended = models.DateField(null=True)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=PROTECT, null=True)
    revision = models.PositiveIntegerField(default=1)
    birth_date = models.DateField(null=True, blank=True)
    nationality = models.CharField(max_length=80, blank=True)
    address = models.CharField(max_length=500, blank=True)
    national_id_encrypted = models.TextField(blank=True)
    national_id_masked = models.CharField(max_length=8, blank=True)

    department_ref = models.ForeignKey(HRReference, null=True, on_delete=PROTECT, related_name="+")
    title_ref = models.ForeignKey(HRReference, null=True, on_delete=PROTECT, related_name="+")
    grade_ref = models.ForeignKey(HRReference, null=True, on_delete=PROTECT, related_name="+")
    archived_at = models.DateTimeField(null=True)


class Offboarding(models.Model):
    employee = models.ForeignKey(Employee, on_delete=PROTECT)
    kind = models.CharField(max_length=16)
    last_day = models.DateField()
    reason = models.CharField(max_length=500)
    state = models.CharField(max_length=12, default="submitted")
    revision = models.PositiveIntegerField(default=1)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=PROTECT, related_name="+")
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=PROTECT, null=True, related_name="+")
    decided_at = models.DateTimeField(null=True)
    note = models.CharField(max_length=500, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["employee"],
            condition=models.Q(state__in=("submitted", "approved")), name="hr_one_active_offboarding")]


class Compensation(models.Model):
    employee = models.ForeignKey(Employee, on_delete=PROTECT, related_name="terms")
    effective = models.DateField()
    center = models.ForeignKey("accounting.Reference", on_delete=PROTECT)
    basic = models.DecimalField(max_digits=17, decimal_places=2)
    allowance = models.DecimalField(max_digits=17, decimal_places=2)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["employee", "effective"], name="hr_unique_effective_term")]


class LeaveAdjustment(models.Model):
    employee = models.ForeignKey(Employee, on_delete=PROTECT)
    day = models.DateField()
    days = models.IntegerField()
    reason = models.CharField(max_length=500)


class Leave(models.Model):
    employee = models.ForeignKey(Employee, on_delete=PROTECT)
    site = models.ForeignKey("backbone.Site", on_delete=PROTECT)
    start = models.DateField()
    end = models.DateField()
    paid = models.BooleanField()
    reason = models.CharField(max_length=500)
    state = models.CharField(max_length=12, default="pending")
    decision = models.CharField(max_length=500, blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=PROTECT)


class PayrollRun(models.Model):
    site = models.ForeignKey("backbone.Site", on_delete=PROTECT)
    month = models.DateField()
    state = models.CharField(max_length=12, default="draft")
    revision = models.PositiveIntegerField(default=1)
    prepared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=PROTECT, related_name="+")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=PROTECT, null=True, related_name="+")
    digest = models.CharField(max_length=64, blank=True)
    preview = models.JSONField(default=dict)
    review_note = models.CharField(max_length=500, blank=True)
    entry = models.OneToOneField("accounting.Entry", on_delete=PROTECT, null=True, related_name="payroll_run")
    reversal = models.OneToOneField("accounting.Entry", on_delete=PROTECT, null=True, related_name="payroll_reversal")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["site", "month"], condition=~models.Q(state="reversed"),
                                               name="payroll_one_active_site_month")]


class PayrollInput(models.Model):
    run = models.ForeignKey(PayrollRun, on_delete=PROTECT, related_name="inputs")
    employee = models.ForeignKey(Employee, on_delete=PROTECT)
    kind = models.CharField(max_length=16)
    amount = models.DecimalField(max_digits=17, decimal_places=2)
    reason = models.CharField(max_length=500)
    reference = models.CharField(max_length=100)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=PROTECT, null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["run", "employee", "reference"], name="payroll_input_reference")]


class Payslip(models.Model):
    run = models.ForeignKey(PayrollRun, on_delete=PROTECT, related_name="slips")
    employee = models.ForeignKey(Employee, on_delete=PROTECT)
    month = models.DateField()
    center = models.ForeignKey("accounting.Reference", on_delete=PROTECT)
    snapshot = models.JSONField()
    gross = models.DecimalField(max_digits=22, decimal_places=2)
    deduction = models.DecimalField(max_digits=22, decimal_places=2)
    net = models.DecimalField(max_digits=22, decimal_places=2)
    reserved = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["run", "employee"], name="payroll_one_slip_per_employee"),
            models.UniqueConstraint(fields=["employee", "month"], condition=models.Q(reserved=True),
                                    name="payroll_one_employee_month"),
        ]


class Payment(models.Model):
    run = models.ForeignKey(PayrollRun, on_delete=PROTECT, related_name="payments")
    entry = models.OneToOneField("accounting.Entry", on_delete=PROTECT)
    amount = models.DecimalField(max_digits=22, decimal_places=2)
    reference = models.CharField(max_length=100, unique=True)


class AttendancePolicy(models.Model):
    site = models.OneToOneField("backbone.Site", on_delete=PROTECT)
    week_start = models.PositiveSmallIntegerField(default=0)
    standard_minutes = models.PositiveSmallIntegerField(default=480)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(week_start__lte=6), name="attendance_weekday_valid"),
            models.CheckConstraint(condition=models.Q(standard_minutes__range=(1, 1440)), name="attendance_standard_valid"),
        ]


class WeeklyTimesheet(models.Model):
    employee = models.ForeignKey(Employee, on_delete=PROTECT)
    site = models.ForeignKey("backbone.Site", on_delete=PROTECT)
    week_start = models.DateField()
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    timezone = models.CharField(max_length=80)
    standard_minutes = models.PositiveSmallIntegerField()
    state = models.CharField(max_length=12, default="draft")
    revision = models.PositiveIntegerField(default=1)
    snapshot = models.JSONField(default=dict)
    overtime_requests = models.JSONField(default=dict)
    review_note = models.CharField(max_length=500, blank=True)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=PROTECT, null=True, related_name="+")
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=PROTECT, null=True, related_name="+")
    reviewed_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["employee", "starts_at"], name="attendance_employee_week_unique"),
            models.CheckConstraint(condition=models.Q(ends_at__gt=models.F("starts_at")), name="attendance_week_order"),
        ]
        indexes = [models.Index(fields=["site", "state", "week_start"], name="attendance_review_idx")]


class AttendanceSession(models.Model):
    employee = models.ForeignKey(Employee, on_delete=PROTECT)
    site = models.ForeignKey("backbone.Site", on_delete=PROTECT)
    timezone = models.CharField(max_length=80)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["employee"], condition=models.Q(ended_at__isnull=True),
                                    name="attendance_one_open_session"),
            models.CheckConstraint(condition=models.Q(ended_at__isnull=True) | models.Q(ended_at__gte=models.F("started_at")),
                                   name="attendance_session_order"),
        ]
        indexes = [models.Index(fields=["employee", "started_at"], name="attendance_session_history")]


class AttendanceBreak(models.Model):
    session = models.ForeignKey(AttendanceSession, on_delete=PROTECT, related_name="breaks")
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["session"], condition=models.Q(ended_at__isnull=True),
                                    name="attendance_one_open_break"),
            models.CheckConstraint(condition=models.Q(ended_at__isnull=True) | models.Q(ended_at__gte=models.F("started_at")),
                                   name="attendance_break_order"),
        ]


class WorkOrderTimer(models.Model):
    employee = models.ForeignKey(Employee, on_delete=PROTECT)
    session = models.ForeignKey(AttendanceSession, on_delete=PROTECT, related_name="timers")
    order = models.ForeignKey("workorders.WorkOrder", on_delete=PROTECT)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["employee"], condition=models.Q(ended_at__isnull=True),
                                    name="attendance_one_open_timer"),
            models.CheckConstraint(condition=models.Q(ended_at__isnull=True) | models.Q(ended_at__gte=models.F("started_at")),
                                   name="attendance_timer_order"),
        ]


class TimesheetApprovalHistory(models.Model):
    sheet = models.ForeignKey(WeeklyTimesheet, on_delete=PROTECT, related_name="approval_history")
    approved_revision = models.PositiveIntegerField()
    snapshot = models.JSONField()
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=PROTECT, null=True, related_name="+")
    approved_at = models.DateTimeField(null=True)
    reopened_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=PROTECT, related_name="+")
    reopened_at = models.DateTimeField()
    reason = models.CharField(max_length=500)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["sheet", "approved_revision"],
                                               name="attendance_archived_revision")]


class AttendanceCorrection(models.Model):
    session = models.ForeignKey(AttendanceSession, on_delete=PROTECT, related_name="corrections")
    site = models.ForeignKey("backbone.Site", on_delete=PROTECT)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=PROTECT, related_name="+")
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=PROTECT, null=True, related_name="+")
    requested_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True)
    revision = models.PositiveIntegerField(default=1)
    state = models.CharField(max_length=10, default="pending")
    reason = models.CharField(max_length=500)
    review_note = models.CharField(max_length=500, blank=True)
    original = models.JSONField()
    proposed = models.JSONField()
    affects_from = models.DateTimeField()
    affects_until = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["session"], condition=models.Q(state="pending"),
                                               name="attendance_pending_correction")]
        indexes = [models.Index(fields=["site", "state"], name="attendance_correction_review")]
