from decimal import Decimal
from datetime import datetime

from django.db.models import F, Q
from django.utils import timezone

from backbone.access import audit, has_permission
from backbone.http import Problem, identifier
from backbone.registry import enabled_modules
from hr.models import WorkOrderTimer
from inventory.stock import rounded

from .access import accessible, member
from .models import Project, Task, TimeAllocation


def instant(value, field):
    try:
        result = datetime.fromisoformat(value)
        if timezone.is_aware(result):
            return result
    except (TypeError,ValueError,OverflowError):
        pass
    raise Problem("invalid_"+field,field=field)


def seconds(row):
    start,end = (row.timer.started_at,row.timer.ended_at) if row.timer_id else (row.start,row.end)
    return int((end-start).total_seconds())


def selected(user,sheet):
    rows = TimeAllocation.objects.filter(sheet=sheet,task__project__in=accessible(user)).select_related("task__project","timer")
    return [{"id":row.pk,"project":row.task.project.name,"task":row.task.title,"task_id":row.task_id,
             "project_id":row.task.project_id,"timer_id":row.timer_id,
             "start":row.start,"end":row.end,"seconds":row.approved_seconds if sheet.state=="approved" else seconds(row)}
            for row in rows.order_by("pk")]


def completed_timers(sheet):
    return WorkOrderTimer.objects.filter(employee_id=sheet.employee_id, session__site_id=sheet.site_id,
        started_at__gte=sheet.starts_at, started_at__lt=sheet.ends_at, ended_at__gt=F("started_at"))


def options(user, sheet):
    empty = {"tasks": [], "timers": []}
    if sheet.employee.user_id != user.pk:
        return {**empty, "status": "not_owner"}
    if sheet.state != "draft":
        return {**empty, "status": "locked"}
    if not all(has_permission(user, permission, sheet.site_id)
               for permission in ("attendance.record", "projects.view", "projects.time")):
        return {**empty, "status": "permission"}
    projects = accessible(user).filter(site_id=sheet.site_id, state="active")
    if not projects.exists():
        return {**empty, "status": "no_projects"}
    tasks = [{"id": task.pk, "name": f"{task.project.code} · {task.title}"}
             for task in Task.objects.filter(project__in=projects, milestone=False).select_related("project")
             .order_by("project__code", "title", "pk")]
    if not tasks:
        return {**empty, "status": "no_tasks"}
    timers = completed_timers(sheet).filter(Q(timeallocation__isnull=True) | Q(timeallocation__sheet=sheet))
    return {"status": "ready", "tasks": tasks,
            "timers": [{"id": row.pk, "order_id": row.order_id, "seconds": int((row.ended_at-row.started_at).total_seconds())}
                       for row in timers.order_by("started_at", "pk")]}



def change(sheet,user,data):
    rows = TimeAllocation.objects.filter(sheet=sheet)
    key = identifier(data.get("id"),"id",optional=True)
    current = rows.filter(pk=key).first() if key else None
    if key and current is None:
        raise Problem("not_found",404)
    task_id = identifier(data.get("task_id"),"task_id",optional=data.get("action")=="remove")
    task = Task.objects.filter(pk=task_id,project__in=accessible(user)).select_related("project").first() if task_id else None
    if task_id and (task is None or task.project.site_id != sheet.site_id):
        raise Problem("not_found",404)
    projects = {row for row in (task.project_id if task else None,current.task.project_id if current else None) if row}
    locked = {row.pk:row for row in Project.objects.select_for_update().filter(pk__in=projects).order_by("pk")}
    for row in locked.values():
        member(row,user,"time")
        if row.state!="active":
            raise Problem("project_inactive",409)
    if data.get("action")=="remove":
        if not current:
            raise Problem("not_found",404)
        audit(user,"projects.time.remove",f"{current.task.project.code} / TS-{sheet.pk} / #{current.pk}")
        current.delete()
        return
    if task.milestone:
        raise Problem("project_task_unavailable",409)
    timer_id = identifier(data.get("timer_id"),"timer_id",optional=True)
    timer = None
    start = end = None
    if timer_id:
        timer = completed_timers(sheet).filter(pk=timer_id).first()
        if not timer:
            raise Problem("project_timer_unavailable",409)
        if TimeAllocation.objects.filter(timer=timer).exclude(pk=key).exists():
            raise Problem("project_timer_assigned",409)
        if TimeAllocation.objects.filter(sheet__employee_id=sheet.employee_id,timer__isnull=True,
                start__lt=timer.ended_at,end__gt=timer.started_at).exclude(pk=key).exists():
            raise Problem("project_time_overlap",409)
    else:
        start,end = instant(data.get("start"),"start"),instant(data.get("end"),"end")
        if not sheet.starts_at<=start<end<=min(sheet.ends_at,timezone.now()) or (end-start).total_seconds()<1:
            raise Problem("project_time_outside_week",409)
        if rows.filter(timer__isnull=True,start__lt=end,end__gt=start).exclude(pk=key).exists() or WorkOrderTimer.objects.filter(
                employee_id=sheet.employee_id,started_at__lt=end).filter(Q(ended_at__gt=start)|Q(ended_at__isnull=True)).exists():
            raise Problem("project_time_overlap",409)
    current = current or TimeAllocation(sheet=sheet)
    current.task,current.timer,current.start,current.end = task,timer,start,end
    current.save()
    audit(user,"projects.time.save",f"{task.project.code} / TS-{sheet.pk} / #{current.pk}")


def transition(sheet, actor, action):
    rows = list(TimeAllocation.objects.filter(sheet=sheet).select_related("task__project","timer").order_by("pk"))
    if not rows:
        return
    if "projects" not in enabled_modules():
        raise Problem("module_disabled",404)
    locked = {p.pk:p for p in Project.objects.select_for_update().filter(
        pk__in={row.task.project_id for row in rows}).order_by("pk")}
    for project in locked.values():
        if action=="submit":
            member(project,actor,"time")
        elif action in ("approve","return","reopen"):
            member(project,actor,"review")
        if project.state!="active" and action in ("submit","approve","reopen"):
            raise Problem("project_inactive",409)
        audit(actor,"projects.time."+action,f"{project.code} / TS-{sheet.pk}")
    if action=="approve":
        for row in rows:
            project = locked[row.task.project_id]
            person = member(project,sheet.employee.user,"time")
            rate = project.rates.filter(role=person.role).values_list("rate",flat=True).first()
            if rate is None:
                raise Problem("project_rate_required",409)
            row.approved_seconds = seconds(row)
            row.approved_rate = rate
            row.approved_cost = rounded(Decimal(row.approved_seconds)*rate/3600)
        TimeAllocation.objects.bulk_update(rows,["approved_seconds","approved_rate","approved_cost"])


def reviewable(rows,user):
    from django.db.models import Exists, OuterRef
    from backbone.access import scopes
    from .models import Membership

    memberships = Membership.objects.filter(user=user,role__in=("manager","finance"),active=True)
    sites = scopes(user,"projects.view")
    review_sites = scopes(user,"projects.review")
    if None not in sites:
        memberships = memberships.filter(project__site_id__in=sites)
    if None not in review_sites:
        memberships = memberships.filter(project__site_id__in=review_sites)
    blocked = TimeAllocation.objects.filter(sheet_id=OuterRef("pk")).exclude(
        task__project_id__in=memberships.values("project_id"))
    return rows.filter(~Exists(blocked))
