from datetime import timedelta
import re

from django.contrib.auth import get_user_model
from django.db.models import Max, Q
from django.utils import timezone
from django.views.decorators.cache import never_cache

from backbone.access import audit, has_permission, visible
from backbone.http import Problem, body, endpoint, identifier, once, require, string
from backbone.registry import enabled_modules
from backbone.services.outbox import enqueue, workspace_status
from backbone.services.pagination import paginate, positive_filter, search_rows
from backbone.site_admin import require_active_site

from .access import can_use_internal_notes, is_agent, project_tasks_visible, site_staff, tickets_for, work_visible
from .models import Category, Comment, SLAPolicy, Ticket, TicketEvent, TicketLink

MODULE = "helpdesk"
DEFAULTS = {"low":(240,2880),"normal":(120,1440),"high":(60,480),"urgent":(30,240)}
STATES = ("open","in_progress","waiting","resolved","closed")


def one(rows, key):
    row = rows.filter(pk=key).first()
    if row is None:
        raise Problem("not_found",404)
    return row


def priority(data):
    key = data.get("priority")
    if not isinstance(key, str) or key not in DEFAULTS:
        raise Problem("invalid_priority",field="priority")
    return key


def dates(row):
    return {key:(value.isoformat() if value else None) for key in
            ("created_at","first_response_due","resolution_due","first_response_at","resolved_at","closed_at")
            for value in [getattr(row,key,None)]}


def ticket_data(row):
    now = timezone.now()
    return {"id":row.pk,"number":row.number,"site_id":row.site_id,"requester_id":row.requester_id,
            "requester":row.requester.get_username(),"assignee_id":row.assignee_id,
            "assignee":row.assignee.get_username() if row.assignee_id else None,
            "subject":row.subject,"description":row.description,"category_id":row.category_id,"priority":row.priority,
            "status":row.status,"revision":row.revision,**dates(row),
            "overdue":(row.resolved_at is None and row.status!="closed" and (
                row.resolution_due<now or (row.first_response_at is None and row.first_response_due<now)))}


def scoped(user, lock=False):
    rows = tickets_for(user).select_related("requester","assignee")
    return rows.select_for_update(of=("self",)) if lock else rows


def check_revision(row, data):
    if row.revision != identifier(data.get("revision"),"revision"):
        raise Problem("revision_conflict",409)


def event(row,user,kind,previous="",current="",reason="",internal=False,recipients=()):
    item = TicketEvent.objects.create(ticket=row,actor=user,event_type=kind,previous=previous,current=current,
                                      reason=reason,internal=internal)
    audit(user,"helpdesk."+kind,f"{row.number} / #{item.pk}")
    if recipients:
        enqueue(key=f"helpdesk:{item.pk}",module=MODULE,object_id=row.pk,event="helpdesk_"+kind,
                recipients={key for key in recipients if key!=user.pk})
    return item


def category(data, site):
    key = identifier(data.get("category_id"),"category_id",optional=True)
    return one(Category.objects.filter(site_id=site,active=True),key) if key else None


@never_cache
@endpoint("GET","POST",module=MODULE)
def tickets(request):
    user = request.user
    if request.method=="GET":
        require(user,"helpdesk.view")
        rows = scoped(user)
        term = request.GET.get("q","").strip()
        ticket_number = re.fullmatch(r"HD-([0-9]{1,18})", term, re.IGNORECASE)
        rows = rows.filter(pk=int(ticket_number[1])) if ticket_number else search_rows(rows,request.GET,("subject",))
        if category_id := positive_filter(request.GET,"category"):
            rows = rows.filter(category_id=category_id)
        if site:=positive_filter(request.GET,"site"):
            rows = rows.filter(site_id=site)
        if status:=request.GET.get("status"):
            rows = rows.filter(status=status)
        if order_priority:=request.GET.get("priority"):
            rows = rows.filter(priority=order_priority)
        if assignee:=positive_filter(request.GET,"assignee"):
            rows = rows.filter(assignee_id=assignee)
        if request.GET.get("overdue")=="1":
            now=timezone.now()
            rows=rows.exclude(status__in=("resolved","closed")).filter(
                Q(resolution_due__lt=now)|Q(first_response_at__isnull=True,first_response_due__lt=now))
        return paginate(request,rows.order_by("-pk"),ticket_data)
    data = body(request)
    site = identifier(data.get("site_id"),"site_id")
    require(user,"helpdesk.view",site)
    require(user,"helpdesk.request",site)

    def create():
        require_active_site(site,lock=True)
        level=priority(data)
        policy=SLAPolicy.objects.filter(site_id=site,priority=level).order_by("-version").first()
        response,resolution=(policy.first_response_minutes,policy.resolution_minutes) if policy else DEFAULTS[level]
        now=timezone.now()
        row=Ticket.objects.create(site_id=site,requester=user,subject=string(data,"subject",140),
             description=string(data,"description",10000),category=category(data,site),priority=level,policy=policy,
             created_at=now,first_response_due=now+timedelta(minutes=response),resolution_due=now+timedelta(minutes=resolution))
        event(row,user,"opened",current="open",recipients=site_staff(site,"helpdesk.dispatch"))
        return ticket_data(row)
    return once(request,"helpdesk.open",data,create)


def links(row,user):
    active=enabled_modules()
    results=[]
    for link in row.links.select_related("work_order"):
        if link.work_order_id and "workorders" in active and work_visible(user).filter(pk=link.work_order_id).exists():
            results.append({"id":link.pk,"label":f"WO-{link.work_order_id} · {link.work_order.title}","route":f"/work-orders/{link.work_order_id}"})
        elif link.project_task_id and "projects" in active:
            task = project_tasks_visible(user).filter(pk=link.project_task_id).first()
            if task:
                results.append({"id":link.pk,"label":task.title,"route":f"/projects/{task.project_id}"})
    return results


EDGES = {
    "open":("in_progress",), "in_progress":("waiting","resolved"),
    "waiting":("in_progress","resolved"), "resolved":("closed","in_progress"),
    "closed":("in_progress",),
}


def allowed_transitions(row,user):
    return [target for target in EDGES[row.status]
            if (row.status in ("resolved","closed") and target=="in_progress" and
                has_permission(user,"helpdesk.reopen",row.site_id)) or
               ((row.status not in ("resolved","closed") or target=="closed") and
                (is_agent(user,row.site_id) or (target=="closed" and row.requester_id==user.pk)))]


@never_cache
@endpoint("GET","PATCH",module=MODULE)
def ticket(request,pk):
    user=request.user
    row=one(scoped(user),pk)
    if request.method=="GET":
        agent=is_agent(user,row.site_id)
        dispatch=has_permission(user,"helpdesk.dispatch",row.site_id)
        result={**ticket_data(row),"links":links(row,user),"can_assign":dispatch,"agent":agent,
                "can_use_internal_notes":can_use_internal_notes(user,row),
                "can_edit":row.status=="open" and (agent or row.requester_id==user.pk),"transitions":allowed_transitions(row,user)}
        if dispatch:
            ids=set(site_staff(row.site_id,"helpdesk.agent"))
            result["candidates"]=[{"id":worker.pk,"name":worker.get_username()} for worker in get_user_model().objects.filter(pk__in=ids).order_by("username")
                                  if has_permission(worker,"helpdesk.view",row.site_id)]
            active=enabled_modules()
            result["work_orders"]=[{"id":key,"name":f"WO-{key} · {title}"} for key,title in work_visible(user).filter(
                site_id=row.site_id).order_by("-pk").values_list("id","title")[:100]] if "workorders" in active else []
            result["project_tasks"]=[{"id":key,"name":f"{code} · {title}"} for key,code,title in project_tasks_visible(user).filter(
                project__site_id=row.site_id,milestone=False).order_by("-pk").values_list("id","project__code","title")[:100]] if "projects" in active else []
        return result
    return mutate(request,pk,"edit")


@never_cache
@endpoint("POST",module=MODULE)
def action(request,pk,operation):
    return mutate(request,pk,operation)


def mutate(request,pk,operation):
    user,data=request.user,body(request)
    initial=one(scoped(user),pk)
    if operation in ("assign","links"):
        require(user,"helpdesk.dispatch",initial.site_id)

    def apply():
        require_active_site(initial.site_id,lock=True)
        row=one(scoped(user,True),pk)
        check_revision(row,data)
        if operation=="edit":
            if row.status!="open" or (row.requester_id!=user.pk and not is_agent(user,row.site_id)):
                raise Problem("helpdesk_invalid_state",409)
            row.subject=string(data,"subject",140)
            row.description=string(data,"description",10000)
            row.category=category(data,row.site_id)
            event(row,user,"edited")
        elif operation=="assign":
            if row.status in ("resolved","closed"):
                raise Problem("helpdesk_invalid_state",409)
            old=row.assignee_id
            person=one(get_user_model().objects.filter(is_active=True),identifier(data.get("assignee_id"),"assignee_id"))
            require(person,"helpdesk.view",row.site_id)
            require(person,"helpdesk.agent",row.site_id)
            row.assignee=person
            event(row,user,"assigned",str(old or ""),str(person.pk),recipients=(person.pk,row.requester_id))
        elif operation=="transition":
            next_status=data.get("status")
            if next_status not in allowed_transitions(row,user):
                raise Problem("helpdesk_invalid_state",409)
            if next_status=="in_progress" and row.assignee_id is None:
                raise Problem("helpdesk_assignee_required",409)
            previous=row.status
            reopen=previous in ("resolved","closed") and next_status=="in_progress"
            reason=string(data,"reason",500,required=reopen)
            now=timezone.now()
            if reopen:
                row.resolved_at=row.closed_at=None
            elif next_status=="resolved":
                row.resolved_at=now
            elif next_status=="closed":
                row.closed_at=now
            row.status=next_status
            event(row,user,"resolved" if next_status=="resolved" else "transition",previous,next_status,reason,
                  recipients=(row.requester_id,row.assignee_id))
        elif operation=="links":
            if row.status=="closed":
                raise Problem("helpdesk_invalid_state",409)
            active=enabled_modules()
            key=identifier(data.get("object_id"),"object_id")
            kind=data.get("type")
            if kind=="work_order" and "workorders" in active:
                fields={"work_order":one(work_visible(user).filter(site_id=row.site_id),key)}
            elif kind=="project_task" and "projects" in active:
                fields={"project_task_id":one(project_tasks_visible(user).filter(project__site_id=row.site_id,milestone=False),key).pk}
            else:
                raise Problem("invalid_type")
            _,fresh=TicketLink.objects.get_or_create(ticket=row,**fields,defaults={"linked_by":user})
            if fresh:
                event(row,user,"linked",internal=True)
        else:
            raise Problem("not_found",404)
        row.revision+=1
        row.save()
        return ticket_data(row)
    return once(request,f"helpdesk.{operation}",{**data,"id":pk},apply)


def comment_data(row):
    return {"id":row.pk,"author":row.author.get_username(),"body":row.body,"internal":row.internal,"created_at":row.created_at.isoformat()}


@never_cache
@endpoint("GET","POST",module=MODULE)
def comments(request,pk):
    user=request.user
    row=one(scoped(user),pk)
    data=body(request) if request.method=="POST" else None
    if request.method=="GET":
        values=row.comments.select_related("author")
        if not can_use_internal_notes(user,row):
            values=values.filter(internal=False)
        return paginate(request,values.order_by("pk"),comment_data)
    internal=data.get("internal",False)
    if type(internal) is not bool or internal and not can_use_internal_notes(user,row):
        raise Problem("forbidden",403)

    def add():
        require_active_site(row.site_id,lock=True)
        locked=one(scoped(user,True),pk)
        if locked.status in ("resolved","closed"):
            raise Problem("helpdesk_invalid_state",409)
        note=Comment.objects.create(ticket=locked,author=user,body=string(data,"body",8000),internal=internal)
        if not internal and user.pk!=locked.requester_id and not locked.first_response_at and is_agent(user,locked.site_id):
            locked.first_response_at=note.created_at
            locked.revision+=1
            locked.save(update_fields=["first_response_at","revision"])
        recipients=() if internal else (locked.requester_id,locked.assignee_id)
        event(locked,user,"commented",internal=internal,recipients=recipients)
        return comment_data(note)
    return once(request,"helpdesk.comment",{**data,"id":pk},add)


@never_cache
@endpoint("GET",module=MODULE)
def events(request,pk):
    user=request.user
    row=one(scoped(user),pk)
    rows=row.events.select_related("actor")
    if not can_use_internal_notes(user,row):
        rows=rows.filter(internal=False)
    return paginate(request,rows.order_by("-pk"),lambda item: {
        "id":item.pk,"event":item.event_type,"actor":item.actor.get_username() if item.actor else None,"previous":item.previous,
        "current":item.current,"reason":item.reason,"created_at":item.created_at.isoformat(),
    })


def config_data(row):
    keys=("site_id","id","code","name_ar","name_en","active","revision") if isinstance(row,Category) else (
        "site_id","id","priority","first_response_minutes","resolution_minutes","version")
    return {name:getattr(row,name) for name in keys}


@never_cache
@endpoint("GET","POST",module=MODULE)
def configuration(request,kind):
    user=request.user
    model=Category if kind=="categories" else SLAPolicy
    if request.method=="GET":
        permission="helpdesk.view" if model is Category else "helpdesk.configure"
        require(user,permission)
        rows=visible(model.objects.all(),user,permission)
        if site:=positive_filter(request.GET,"site"):
            rows=rows.filter(site_id=site)
        if model is Category and request.GET.get("all")!="1":
            rows=rows.filter(active=True)
        elif model is Category:
            rows=visible(rows,user,"helpdesk.configure")
        return paginate(request,rows.order_by("-pk"),config_data)
    data=body(request)
    site=identifier(data.get("site_id"),"site_id")
    require(user,"helpdesk.configure",site)

    def create():
        require_active_site(site,lock=True)
        if model is Category:
            code=string(data,"code",30).upper()
            if not re.fullmatch(r"[A-Z0-9_-]+",code):
                raise Problem("invalid_code",field="code")
            row,fresh=Category.objects.get_or_create(site_id=site,code=code,defaults={
                "name_ar":string(data,"name_ar",80),"name_en":string(data,"name_en",80)})
            if not fresh:
                raise Problem("helpdesk_duplicate_category",409)
        else:
            key=priority(data)
            response=identifier(data.get("first_response_minutes"),"first_response_minutes")
            resolve=identifier(data.get("resolution_minutes"),"resolution_minutes")
            if response>resolve or resolve>525600:
                raise Problem("invalid_resolution_minutes",field="resolution_minutes")
            version=(SLAPolicy.objects.filter(site_id=site,priority=key).aggregate(latest=Max("version"))["latest"] or 0)+1
            row=SLAPolicy.objects.create(site_id=site,priority=key,first_response_minutes=response,resolution_minutes=resolve,version=version)
        audit(user,"helpdesk."+kind,f"#{row.pk}")
        return config_data(row)
    return once(request,"helpdesk."+kind,data,create)


@never_cache
@endpoint("PATCH",module=MODULE)
def category_detail(request,pk):
    user=request.user
    row=one(visible(Category.objects.all(),user,"helpdesk.configure"),pk)
    data=body(request)

    def edit():
        require_active_site(row.site_id,lock=True)
        item=one(visible(Category.objects.select_for_update(),user,"helpdesk.configure"),pk)
        check_revision(item,data)
        item.name_ar=string(data,"name_ar",80)
        item.name_en=string(data,"name_en",80)
        if type(data.get("active")) is not bool:
            raise Problem("invalid_active",field="active")
        item.active=data["active"]
        item.revision+=1
        item.save()
        audit(user,"helpdesk.category.edit",f"#{row.pk}")
        return config_data(item)
    return once(request,"helpdesk.category.edit",{**data,"id":pk},edit)


@never_cache
@endpoint("GET")
def delivery_health(request):
    return workspace_status(request.user)
