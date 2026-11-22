from django.contrib.auth import get_user_model
from django.db import transaction
from django.views.decorators.cache import never_cache

from backbone.http import Problem, body, endpoint, string
from backbone.models import SavedView
from .list_data import FILTERS, list_filters
from .report_filters import BOARDS, valid_filters


def serialize(row):
    return {"id": row.pk, "name": row.name, "filters": row.filters, "is_default": row.is_default}


def screen_rows(user, screen):
    if screen != "budget-vs-actual" and screen not in FILTERS and not (screen.startswith("reports-") and screen[8:] in BOARDS):
        raise Problem("not_found", 404)
    return SavedView.objects.filter(user=user, screen=screen)


def save(request, rows, screen, pk=None):
    data = body(request)
    name = string(data, "name", 80)
    if screen == "budget-vs-actual":
        from accounting.budgeting import report_filters

        raw = data.get("filters")
        if not isinstance(raw, dict) or any(not isinstance(value, str) for value in raw.values()):
            raise Problem("report_invalid_filters")
        filters = report_filters(raw)
    else:
        filters = (valid_filters(screen[8:], data.get("filters")) if screen.startswith("reports-")
                   else list_filters(screen, data.get("filters")))
    default = data.get("is_default", False)
    if type(default) is not bool:
        raise Problem("invalid_view_default")
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=request.user.pk)
        row = rows.filter(pk=pk).first() if pk else SavedView(user=request.user, screen=screen)
        if row is None:
            raise Problem("not_found", 404)
        if rows.exclude(pk=pk).filter(name=name).exists():
            raise Problem("saved_view_name_exists", 409)
        if default:
            rows.filter(is_default=True).exclude(pk=pk).update(is_default=False)
        row.name, row.filters, row.is_default = name, filters, default
        row.save()
        return serialize(row), 200 if pk else 201


@never_cache
@endpoint("GET", "POST")
def views(request, screen):
    rows = screen_rows(request.user, screen)
    if request.method == "GET":
        return {"items": [serialize(row) for row in rows.order_by("name", "id")]}
    return save(request, rows, screen)


@never_cache
@endpoint("PATCH", "DELETE")
def view(request, screen, pk):
    rows = screen_rows(request.user, screen)
    if request.method == "PATCH":
        return save(request, rows, screen, pk)
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=request.user.pk)
        if not rows.filter(pk=pk).delete()[0]:
            raise Problem("not_found", 404)
    return {"deleted": True}
