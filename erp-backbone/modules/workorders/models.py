from django.db import models


class WorkOrder(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        IN_REVIEW = "in_review", "In review"
        APPROVED = "approved", "Approved"

    title = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    site = models.ForeignKey("backbone.Site", on_delete=models.PROTECT, null=True)
    asset_reference = models.PositiveBigIntegerField(null=True)
    asset_snapshot = models.CharField(max_length=185, blank=True)
    status = models.CharField(max_length=12, choices=Status, default=Status.DRAFT)
    revision = models.PositiveIntegerField(default=1)
    submitted_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)


class ApprovalTemplate(models.Model):
    version = models.PositiveIntegerField(unique=True)
    steps = models.JSONField()
    published_at = models.DateTimeField(auto_now_add=True)


class ApprovalRun(models.Model):
    class Outcome(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        RETURNED = "returned", "Returned"

    order = models.ForeignKey(WorkOrder, on_delete=models.PROTECT, related_name="approval_runs")
    template = models.ForeignKey(ApprovalTemplate, on_delete=models.PROTECT)
    submitted_by = models.ForeignKey("auth.User", on_delete=models.PROTECT)
    submitted_at = models.DateTimeField(auto_now_add=True)
    next_step = models.PositiveIntegerField(default=0)
    outcome = models.CharField(max_length=12, choices=Outcome, default=Outcome.PENDING)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["order"], condition=models.Q(outcome="pending"),
                                    name="one_pending_work_order_approval"),
        ]


class ApprovalDecision(models.Model):
    run = models.ForeignKey(ApprovalRun, on_delete=models.PROTECT, related_name="decisions")
    step = models.PositiveIntegerField()
    actor = models.CharField(max_length=150)
    action = models.CharField(max_length=12, choices=(
        ("approve", "Approve"), ("return", "Return for changes"),
    ))
    reason = models.CharField(max_length=1000, blank=True)
    delegation = models.ForeignKey("backbone.Delegation", on_delete=models.PROTECT, null=True)
    decided_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["run", "step"], name="one_decision_per_approval_step")]


class MaintenancePlan(models.Model):
    asset = models.ForeignKey("assets.Asset", on_delete=models.PROTECT)
    title = models.CharField(max_length=120)
    first_due = models.DateField()
    interval = models.PositiveSmallIntegerField()
    frequency = models.CharField(max_length=8)
    checklist = models.JSONField()
    spare_parts = models.JSONField(default=list)
    active = models.BooleanField(default=True)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        indexes = [
            models.Index(fields=['active', 'first_due'], name='rpt_plan_active_due'),
        ]


class MaintenanceJob(models.Model):
    cost_center = models.ForeignKey("accounting.Reference", on_delete=models.PROTECT, null=True)
    center_required = models.BooleanField(default=False)
    order = models.OneToOneField(WorkOrder, on_delete=models.PROTECT, related_name="maintenance")
    asset = models.ForeignKey("assets.Asset", on_delete=models.PROTECT)
    plan = models.ForeignKey(MaintenancePlan, on_delete=models.PROTECT, null=True)
    due_date = models.DateField(null=True)
    checklist = models.JSONField(default=list)
    spare_parts = models.JSONField(default=list)
    assignee = models.ForeignKey("auth.User", on_delete=models.PROTECT, null=True)
    stage = models.CharField(max_length=16, default="waiting")
    started_at = models.DateTimeField(null=True)
    completed_at = models.DateTimeField(null=True)
    closed_at = models.DateTimeField(null=True)
    findings = models.TextField(blank=True)
    results = models.JSONField(default=list)
    close_note = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['stage', 'asset', 'due_date'], name='rpt_pm_stage_due'),
            models.Index(fields=['due_date', 'plan'], name='rpt_pm_due_plan'),
        ]
        constraints = [models.UniqueConstraint(fields=["plan", "due_date"], name="maintenance_plan_due_once")]
