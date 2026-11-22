from .models import Project


def open_projects(site_id=None):
    rows = Project.objects.filter(state__in=("active","hold"))
    return rows if site_id is None else rows.filter(site_id=site_id)
