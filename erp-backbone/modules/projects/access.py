from backbone.access import has_permission, visible
from backbone.http import Problem
from .models import Membership, Project

MODULE = "projects"


def accessible(user):
    return visible(Project.objects.all(),user,"projects.view").filter(memberships__user=user,memberships__active=True)


def get_project(user, pk, *, lock=False):
    rows = accessible(user)
    if lock:
        rows = rows.select_for_update(of=("self",))
    row = rows.filter(pk=pk).first()
    if row is None:
        raise Problem("not_found",404)
    return row


def member(project,user,action=None):
    row = Membership.objects.filter(project=project,user=user,active=True).first()
    if row is None or not has_permission(user,"projects.view",project.site_id):
        raise Problem("not_found",404)
    roles = {"manage":("manager",), "cost":("manager","finance"), "review":("manager","finance"),
             "time":("manager","member","finance")}
    if action and (row.role not in roles[action] or not has_permission(user,"projects."+action,project.site_id)):
        raise Problem("forbidden",403)
    return row
