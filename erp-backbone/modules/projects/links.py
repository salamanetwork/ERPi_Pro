from accounting.models import Line
from backbone.access import audit, has_permission
from backbone.http import Problem, identifier, require
from backbone.registry import enabled_modules
from purchasing.models import PurchaseOrder
from workorders.models import WorkOrder

from .access import get_project, member
from .models import ExpenseLink, Project, PurchaseLink, Task, WorkLink


def authorized_sources(project,user):
    site = project.site_id
    data = {"work":[],"purchase":[],"expenses":[]}
    active = enabled_modules()
    if "workorders" in active and has_permission(user,"workorders.view",site) and (
            has_permission(user,"maintenance.supervise",site) or has_permission(user,"workorders.edit",site)):
        rows = WorkOrder.objects.filter(site_id=site).exclude(status="approved")
        data["work"] = list(rows.order_by("-pk").values("id","title")[:100])
    if "purchase_orders" in active and has_permission(user,"purchasing.view",site) and has_permission(user,"purchasing.po.create",site):
        rows = PurchaseOrder.objects.filter(site_id=site,status="draft",currency="AED")
        data["purchase"] = list(rows.order_by("-pk").values("id","number","title","lines")[:100])
    if "accounting" in active and has_permission(user,"accounting.view",None) and has_permission(user,"projects.cost",site):
        rows = Line.objects.filter(site_id=site,account__kind="expense",entry__kind="manual",entry__reversal_of__isnull=True,entry__posted_at__isnull=False)
        data["expenses"] = list(rows.order_by("-pk").values("id","entry_id","entry__description","amount")[:100])
    return data


def link(user,pk,data):
    project = get_project(user,pk)
    member(project,user,"manage")
    typ = data.get("type")
    source = identifier(data.get("source_id"),"source_id")
    site = project.site_id
    if typ=="purchase":
        require(user,"purchasing.view",site)
        require(user,"purchasing.po.create",site)
        if "purchase_orders" not in enabled_modules():
            raise Problem("module_disabled",404)
        doc = PurchaseOrder.objects.select_for_update().filter(pk=source,site_id=site).first()
        line_id = identifier(data.get("line_id"),"line_id")
        if not doc or doc.status!="draft" or doc.currency!="AED" or not any(line["id"]==line_id for line in doc.lines):
            raise Problem("project_link_source",409)
        model,fields = PurchaseLink,{"order":doc,"line_id":line_id}
    elif typ=="work":
        require(user,"workorders.view",site)
        if not (has_permission(user,"maintenance.supervise",site) or has_permission(user,"workorders.edit",site)):
            raise Problem("forbidden",403)
        if "workorders" not in enabled_modules():
            raise Problem("module_disabled",404)
        doc = WorkOrder.objects.select_for_update().filter(pk=source,site_id=site).first()
        if not doc or doc.status=="approved":
            raise Problem("project_link_source",409)
        model,fields = WorkLink,{"order":doc}
    elif typ=="expense":
        member(project,user,"cost")
        require(user,"accounting.view",None)
        if "accounting" not in enabled_modules():
            raise Problem("module_disabled",404)
        line = Line.objects.select_for_update(of=("self",)).filter(pk=source,site_id=site,account__kind="expense",
                                entry__kind="manual",entry__reversal_of__isnull=True,entry__posted_at__isnull=False).first()
        if not line:
            raise Problem("project_link_source",409)
        model,fields = ExpenseLink,{"line":line}
    else:
        raise Problem("invalid_type")
    project = get_project(user,pk,lock=True)
    member(project,user,"manage")
    if project.state not in ("draft","active"):
        raise Problem("project_inactive",409)
    task = Task.objects.filter(pk=identifier(data.get("task_id"),"task_id"),project=project,milestone=False).first()
    if not task:
        raise Problem("not_found",404)
    existing = model.objects.filter(**fields).select_related("task").first()
    if existing and existing.task.project_id!=pk:
        raise Problem("project_source_linked",409)
    if existing and data.get("action")=="remove":
        existing.delete()
        audit(user,"projects.link.remove",f"{project.code} / {typ} / #{source}")
        return {"removed":True}
    if data.get("action")=="remove":
        raise Problem("not_found",404)
    if typ=="expense" and existing:
        raise Problem("project_source_linked",409)
    row,_ = model.objects.update_or_create(**fields,defaults={"task":task})
    audit(user,"projects.link."+typ,f"{project.code} / task #{task.pk} / #{source}")
    return {"id":row.pk}


def ensure_active(ids):
    rows = list(Project.objects.select_for_update().filter(pk__in=ids).order_by("pk"))
    if any(row.state!="active" for row in rows):
        raise Problem("project_inactive",409)


def check_purchases(order_id, line_ids=None):
    rows = PurchaseLink.objects.filter(order_id=order_id)
    if line_ids is not None:
        rows = rows.filter(line_id__in=line_ids)
    ensure_active(rows.values("task__project_id"))


def check_work(order_id):
    ensure_active(WorkLink.objects.filter(order_id=order_id).values("task__project_id"))
