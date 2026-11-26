import re
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import Max, Q
from django.views.decorators.cache import never_cache

from accounting.views import day_field
from backbone.access import audit, has_permission
from backbone.http import Problem, body, endpoint, identifier, once, require, string
from backbone.models import Assignment
from backbone.services.pagination import paginate, positive_filter
from backbone.site_admin import require_active_site
from hr.attendance import visible_sheets
from hr.models import Employee, WeeklyTimesheet
from purchasing.money import decimal_value

from . import costs, links
from .access import MODULE, accessible, get_project, member
from .models import Baseline, LabourRate, Membership, Project, PurchaseLink, Task, TimeAllocation, WorkLink
from .time import change, options

ROLES = ("manager","member","finance")
CATEGORIES = ("materials","expense","labour")


def by_id(rows, key):
    row = rows.filter(pk=key).first()
    if row is None:
        raise Problem("not_found",404)
    return row


def edit_revision(row,data):
    if row.revision!=identifier(data.get("revision"),"revision"):
        raise Problem("revision_conflict",409)
    row.revision+=1


def project_data(project):
    return {"id":project.pk,"name":project.name,"code":project.code,"site_id":project.site_id,
            "start":project.start.isoformat(),"end":project.end.isoformat(),"state":project.state,"revision":project.revision}


def validate_project(data):
    start,end=day_field(data,"start"),day_field(data,"end")
    if end<start:
        raise Problem("project_dates",field="end")
    return {"name":string(data,"name",120),"start":start,"end":end}


def task_data(task):
    return {"id":task.pk,"parent_id":task.parent_id,"owner_id":task.owner_id,"title":task.title,
            "due":task.due.isoformat(),"planned_hours":str(task.planned_hours),"state":task.state,
            "milestone":task.milestone,"revision":task.revision}


def member_data(row):
    return {"id":row.pk,"user_id":row.user_id,"username":row.user.get_username(),"role":row.role,"active":row.active}


@never_cache
@endpoint("GET","POST",module=MODULE)
def index(request):
    user=request.user
    require(user,"projects.view")
    if request.method=="GET":
        rows=accessible(user)
        if site:=positive_filter(request.GET,"site"):
            rows=rows.filter(site_id=site)
        from backbone.services.pagination import search_rows
        return paginate(request,search_rows(rows,request.GET,["code","name"]).order_by("-pk"),project_data)
    data=body(request)
    site=identifier(data.get("site_id"),"site_id")
    require(user,"projects.view",site)
    require(user,"projects.manage",site)
    code=string(data,"code",32).upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]*",code):
        raise Problem("invalid_code",field="code")
    values=validate_project(data)

    def create():
        require_active_site(site,lock=True)
        if Project.objects.filter(site_id=site,code=code).exists():
            raise Problem("project_code_exists",409)
        row=Project.objects.create(site_id=site,code=code,**values)
        Membership.objects.create(project=row,user=user,role="manager")
        audit(user,"projects.create",row.code)
        audit(user,"projects.membership",f"{row.code} / user #{user.pk} / manager")
        return project_data(row)
    return once(request,"projects.create",data,create)


@never_cache
@endpoint("GET",module=MODULE)
def detail(request, pk):
    user=request.user
    row=get_project(user,pk)
    membership=member(row,user)
    can_manage=membership.role=="manager" and has_permission(user,"projects.manage",row.site_id)
    can_cost=membership.role in ("manager","finance") and has_permission(user,"projects.cost",row.site_id)
    result={**project_data(row),"can_manage":can_manage,"can_cost":can_cost,"membership_id":membership.pk,
            "members":[member_data(value) for value in row.memberships.select_related("user").order_by("pk")],
            "tasks":[task_data(value) for value in row.tasks.order_by("due","pk")]}
    result["linked"] = {
        "purchase":[{"id":link.order_id,"line_id":link.line_id,"reference":link.order.number,
                     "state":link.order.status,"task":link.task.title}
                    for link in PurchaseLink.objects.filter(task__project=row).select_related("task","order")],
        "work":[{"id":link.order_id,"reference":f"WO-{link.order_id}","task":link.task.title}
                for link in WorkLink.objects.filter(task__project=row).select_related("task")],
    }
    if can_manage:
        assignments = Assignment.objects.filter(Q(site_id=row.site_id)|Q(site__isnull=True))
        candidates = get_user_model().objects.filter(is_active=True).filter(
            Q(pk__in=assignments.values("user_id"))|Q(is_superuser=True))
        result["candidates"]=[{"id":person.pk,"username":person.get_username()} for person in candidates.order_by("username")
                              if has_permission(person,"projects.view",row.site_id)]
        result["sources"]=links.authorized_sources(row,user) if row.state in ("draft","active") else {}
    if can_cost:
        result["rates"]=list(row.rates.order_by("role").values("role","rate"))
        result["baselines"]=[{"version":value.version,"hours":str(value.hours),"categories":value.categories,
                              "reason":value.reason,"approved_at":value.approved_at} for value in row.baselines.order_by("-version")]
    return result


def transition(row, data):
    action=data.get("action")
    states={"activate":("draft","active"),"hold":("active","hold"),"resume":("hold","active"),
            "close":("active","closed"),"reopen":("closed","active")}
    edge=states.get(action)
    if not edge or edge[0]!=row.state:
        raise Problem("project_transition",409)
    if action=="close":
        if row.tasks.exclude(state="done").exists():
            raise Problem("project_open_tasks",409)
        if TimeAllocation.objects.filter(task__project=row).exclude(sheet__state="approved").exists():
            raise Problem("project_open_time",409)
        if any(value["state"]=="draft" or Decimal(value["committed"])>0 for value in costs.purchase_costs(row)):
            raise Problem("project_open_commitments",409)
        if WorkLink.objects.filter(task__project=row,order__maintenance__isnull=False).exclude(
                order__maintenance__stage__in=("closed","cancelled")).exists():
            raise Problem("project_open_work",409)
    reason=string(data,"reason",500,required=action in ("hold","close","reopen"))
    row.state=edge[1]
    return reason


@never_cache
@endpoint("POST",module=MODULE)
def mutate(request,pk,operation):
    user,data=request.user,body(request)
    initial=get_project(user,pk)
    member(initial,user,"manage")
    if operation=="link":
        return once(request,"projects.link",{"project_id":pk,**data},lambda:links.link(user,pk,data))

    def commit():
        require_active_site(initial.site_id,lock=True)
        row=get_project(user,pk,lock=True)
        member(row,user,"manage")
        edit_revision(row,data)
        information=""
        if operation=="state":
            information=f"{data.get('action')} / {transition(row,data)}"
        else:
            if row.state not in ("draft","active"):
                raise Problem("project_inactive",409)
            if operation=="edit":
                for key,value in validate_project(data).items():
                    setattr(row,key,value)
            elif operation=="member":
                role=data.get("role")
                account=by_id(get_user_model().objects.filter(is_active=True),identifier(data.get("user_id"),"user_id"))
                active=data.get("active")
                if role not in ROLES or type(active) is not bool or (active and not has_permission(account,"projects.view",row.site_id)):
                    raise Problem("project_membership_invalid")
                if not active and TimeAllocation.objects.filter(task__project=row,sheet__employee__user=account).exclude(
                        sheet__state="approved").exists():
                    raise Problem("project_open_time",409)
                if (role!="manager" or not active) and not row.memberships.filter(role="manager",active=True).exclude(user=account).exists():
                    raise Problem("project_needs_manager",409)
                Membership.objects.update_or_create(project=row,user=account,defaults={"role":role,"active":active})
                information=f"user #{account.pk} / {role} / {active}"
            elif operation=="baseline":
                member(row,user,"cost")
                categories=data.get("categories")
                if not isinstance(categories,dict) or set(categories)!=set(CATEGORIES):
                    raise Problem("invalid_categories")
                groups={key:str(decimal_value(categories[key],12,2,key)) for key in CATEGORIES}
                ver=(row.baselines.aggregate(latest=Max("version"))["latest"] or 0)+1
                reason=string(data,"reason",500)
                Baseline.objects.create(project=row,version=ver,approved_by=user,reason=reason,categories=groups,
                                        hours=decimal_value(data.get("hours"),10,2,"hours"))
                information=f"#{ver} / {reason}"
            elif operation=="rate":
                member(row,user,"cost")
                role=data.get("role")
                if role not in ROLES:
                    raise Problem("invalid_role",field="role")
                LabourRate.objects.update_or_create(project=row,role=role,
                            defaults={"rate":decimal_value(data.get("rate"),10,2,"rate")})
                information=role
            else:
                raise Problem("not_found",404)
        row.save()
        audit(user,"projects."+("membership" if operation=="member" else operation),f"{row.code} / {information}")
        return project_data(row)
    return once(request,"projects."+operation,{"project_id":pk,**data},commit)


@never_cache
@endpoint("POST",module=MODULE)
def tasks(request,pk):
    user,data=request.user,body(request)
    initial=get_project(user,pk)

    def commit():
        require_active_site(initial.site_id,lock=True)
        row=get_project(user,pk,lock=True)
        membership=member(row,user)
        if row.state not in ("draft","active"):
            raise Problem("project_inactive",409)
        key=identifier(data.get("id"),"id",optional=True)
        task=by_id(row.tasks.select_for_update(),key) if key else Task(project=row)
        if key:
            edit_revision(task,data)
        state=data.get("state","todo")
        if state not in ("todo","progress","done"):
            raise Problem("invalid_state")
        if data.get("action")=="move":
            if not key:
                raise Problem("not_found",404)
            if task.owner_id!=membership.pk or not has_permission(user,"projects.time",row.site_id):
                member(row,user,"manage")
        else:
            member(row,user,"manage")
            task.title=string(data,"title",120)
            task.due=day_field(data,"due")
            task.planned_hours=decimal_value(data.get("planned_hours"),8,2,"planned_hours")
            owner=identifier(data.get("owner_id"),"owner_id",optional=True)
            task.owner=by_id(row.memberships.filter(active=True),owner) if owner else None
            parent=identifier(data.get("parent_id"),"parent_id",optional=True)
            task.parent=by_id(row.tasks,parent) if parent else None
            cursor=task.parent
            while cursor:
                if cursor.pk==task.pk:
                    raise Problem("project_task_cycle",409)
                cursor=cursor.parent
            task.milestone=data.get("milestone") is True
        if state=="done" and task.pk and row.tasks.filter(parent=task).exclude(state="done").exists():
            raise Problem("project_open_tasks",409)
        task.state=state
        task.save()
        audit(user,"projects.task",f"{row.code} / #{task.pk} / {state}")
        return task_data(task)
    return once(request,"projects.task",{"project_id":pk,**data},commit)


@never_cache
@endpoint("GET",module=MODULE)
def budget(request,pk):
    row=get_project(request.user,pk)
    member(row,request.user,"cost")
    return costs.report(row)


@never_cache
@endpoint("GET","POST",module=MODULE)
def sheet_time(request,pk):
    user=request.user
    sheet=by_id(visible_sheets(user),pk)
    if request.method=="GET":
        return options(user,sheet)
    data=body(request)

    def commit():
        Employee.objects.select_for_update().get(pk=sheet.employee_id)
        row=by_id(WeeklyTimesheet.objects.select_for_update().select_related("employee"),pk)
        require(user,"attendance.record",row.site_id)
        if row.employee.user_id!=user.pk:
            raise Problem("forbidden",403)
        if row.state!="draft":
            raise Problem("attendance_week_locked",409)
        edit_revision(row,data)
        change(row,user,data)
        row.save(update_fields=["revision"])
        return {"sheet_id":row.pk,"revision":row.revision}
    return once(request,"projects.sheet_time",{"sheet_id":pk,**data},commit)
