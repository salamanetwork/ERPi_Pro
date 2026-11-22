from django.conf import settings
from django.db import models

protect = models.PROTECT
user = settings.AUTH_USER_MODEL


class Project(models.Model):
    site = models.ForeignKey("backbone.Site", on_delete=protect)
    code = models.CharField(max_length=32)
    name = models.CharField(max_length=120)
    start = models.DateField()
    end = models.DateField()
    state = models.CharField(max_length=10, default="draft")
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["site","code"],name="project_unique_site_code"),
            models.CheckConstraint(condition=models.Q(end__gte=models.F("start")),name="project_date_order"),
        ]


class Membership(models.Model):
    project = models.ForeignKey(Project,on_delete=protect,related_name="memberships")
    user = models.ForeignKey(user,on_delete=protect)
    role = models.CharField(max_length=12)
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["project","user"],name="project_unique_member")]


class Task(models.Model):
    project = models.ForeignKey(Project,on_delete=protect,related_name="tasks")
    parent = models.ForeignKey("self",on_delete=protect,null=True)
    owner = models.ForeignKey(Membership,on_delete=protect,null=True)
    title = models.CharField(max_length=120)
    due = models.DateField()
    planned_hours = models.DecimalField(max_digits=10,decimal_places=2)
    state = models.CharField(max_length=12,default="todo")
    milestone = models.BooleanField(default=False)
    revision = models.PositiveIntegerField(default=1)


class Baseline(models.Model):
    project = models.ForeignKey(Project,on_delete=protect,related_name="baselines")
    version = models.PositiveIntegerField()
    approved_by = models.ForeignKey(user,on_delete=protect)
    approved_at = models.DateTimeField(auto_now_add=True)
    reason = models.CharField(max_length=500)
    categories = models.JSONField()
    hours = models.DecimalField(max_digits=12,decimal_places=2)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["project","version"],name="project_baseline_version")]


class LabourRate(models.Model):
    project = models.ForeignKey(Project,on_delete=protect,related_name="rates")
    role = models.CharField(max_length=12)
    rate = models.DecimalField(max_digits=12,decimal_places=2)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["project","role"],name="project_rate_role")]


class TimeAllocation(models.Model):
    sheet = models.ForeignKey("hr.WeeklyTimesheet",on_delete=protect,related_name="project_allocations")
    task = models.ForeignKey(Task,on_delete=protect,related_name="time_allocations")
    timer = models.OneToOneField("hr.WorkOrderTimer",on_delete=protect,null=True)
    start = models.DateTimeField(null=True)
    end = models.DateTimeField(null=True)
    approved_seconds = models.PositiveIntegerField(default=0)
    approved_rate = models.DecimalField(max_digits=12,decimal_places=2,null=True)
    approved_cost = models.DecimalField(max_digits=16,decimal_places=2,null=True)

    class Meta:
        constraints = [models.CheckConstraint(
            condition=models.Q(timer__isnull=False,start__isnull=True,end__isnull=True)
                      |models.Q(timer__isnull=True,start__isnull=False,end__isnull=False,end__gt=models.F("start")),
            name="project_time_has_one_source",
        )]


class PurchaseLink(models.Model):
    task = models.ForeignKey(Task,on_delete=protect,related_name="purchases")
    order = models.ForeignKey("purchasing.PurchaseOrder",on_delete=protect)
    line_id = models.PositiveIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["order","line_id"],name="project_one_purchase_line")]


class WorkLink(models.Model):
    task = models.ForeignKey(Task,on_delete=protect,related_name="work_orders")
    order = models.OneToOneField("workorders.WorkOrder",on_delete=protect)


class ExpenseLink(models.Model):
    task = models.ForeignKey(Task,on_delete=protect,related_name="expenses")
    line = models.OneToOneField("accounting.Line",on_delete=protect)
