from django.conf import settings
from django.db import models

PROTECT = models.PROTECT


class Category(models.Model):
    site = models.ForeignKey("backbone.Site", on_delete=PROTECT)
    code = models.SlugField(max_length=30)
    name_ar = models.CharField(max_length=80)
    name_en = models.CharField(max_length=80)
    active = models.BooleanField(default=True)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["site","code"],name="helpdesk_unique_category")]


class SLAPolicy(models.Model):
    site = models.ForeignKey("backbone.Site",on_delete=PROTECT)
    priority = models.CharField(max_length=10)
    first_response_minutes = models.PositiveIntegerField()
    resolution_minutes = models.PositiveIntegerField()
    version = models.PositiveIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["site","priority","version"],name="helpdesk_sla_version")]


class Ticket(models.Model):
    site = models.ForeignKey("backbone.Site",on_delete=PROTECT)
    requester = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=PROTECT,related_name="+")
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=PROTECT,related_name="+",null=True)
    category = models.ForeignKey(Category,on_delete=PROTECT,null=True)
    policy = models.ForeignKey(SLAPolicy,on_delete=PROTECT,null=True)
    priority = models.CharField(max_length=10)
    status = models.CharField(max_length=24,default="open")
    subject = models.CharField(max_length=140)
    description = models.TextField()
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField()
    first_response_due = models.DateTimeField()
    resolution_due = models.DateTimeField()
    first_response_at = models.DateTimeField(null=True)
    resolved_at = models.DateTimeField(null=True)
    closed_at = models.DateTimeField(null=True)

    @property
    def number(self):
        return f"HD-{self.pk:06d}"

    class Meta:
        indexes = [models.Index(fields=["site","status","assignee"],name="helpdesk_queue_idx"),
                   models.Index(fields=["status","resolution_due"],name="helpdesk_due_idx"),
                   models.Index(fields=["status","first_response_due"],name="helpdesk_reply_idx")]


class Comment(models.Model):
    ticket = models.ForeignKey(Ticket,on_delete=PROTECT,related_name="comments")
    author = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=PROTECT)
    body = models.TextField()
    internal = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class TicketEvent(models.Model):
    ticket = models.ForeignKey(Ticket,on_delete=PROTECT,related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=PROTECT,null=True)
    event_type = models.CharField(max_length=24)
    previous = models.CharField(max_length=140,blank=True)
    current = models.CharField(max_length=140,blank=True)
    reason = models.CharField(max_length=500,blank=True)
    internal = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["ticket", "event_type"], name="helpdesk_event_kind_idx")]


class TicketLink(models.Model):
    ticket = models.ForeignKey(Ticket,on_delete=PROTECT,related_name="links")
    work_order = models.ForeignKey("workorders.WorkOrder",on_delete=PROTECT,null=True)
    project_task_id = models.PositiveBigIntegerField(null=True)
    linked_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(work_order__isnull=False,project_task_id__isnull=True) |
                           models.Q(work_order__isnull=True,project_task_id__isnull=False)),name="helpdesk_link_one_type"),
            models.UniqueConstraint(fields=["ticket","work_order"],name="helpdesk_link_work_unique"),
            models.UniqueConstraint(fields=["ticket","project_task_id"],name="helpdesk_link_task_unique"),
        ]
