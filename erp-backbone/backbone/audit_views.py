from datetime import date, datetime, time, timedelta, timezone

from django.views.decorators.cache import never_cache

from .access import audit
from .http import Problem, endpoint, require
from .models import AuditEvent
from .services.csv_export import protect


COLUMNS = ("id", "actor_user_id", "actor", "action", "target", "occurred_at")


def filtered_events(query):
    rows = AuditEvent.objects.order_by("-occurred_at", "-id")
    dates = {}
    for field in ("from", "to"):
        if query.get(field):
            try:
                day = date.fromisoformat(query[field])
                if day.isoformat() != query[field] or day == date.max:
                    raise ValueError
            except ValueError:
                raise Problem("invalid_date") from None
            dates[field] = day
    if dates.get("to", date.max) < dates.get("from", date.min):
        raise Problem("invalid_date")
    if "from" in dates:
        rows = rows.filter(occurred_at__gte=datetime.combine(dates["from"], time.min, timezone.utc))
    if "to" in dates:
        rows = rows.filter(occurred_at__lt=datetime.combine(dates["to"] + timedelta(days=1), time.min, timezone.utc))
    for field in ("actor", "action"):
        if query.get(field):
            if len(query[field]) > 150:
                raise Problem("invalid_filter")
            rows = rows.filter(**{field + "__icontains": query[field]})
    if term:=query.get('q','').strip()[:200]:
        from django.db.models import Q
        rows=rows.filter(Q(actor__icontains=term)|Q(action__icontains=term)|Q(target__icontains=term))
    return rows


@never_cache
@endpoint("GET")
def export(request):
    import csv
    from django.http import StreamingHttpResponse

    require(request.user, "platform.audit", None)
    require(request.user, "platform.export", None)
    rows = filtered_events(request.GET)
    ceiling = AuditEvent.objects.order_by("-pk").values_list("pk", flat=True).first() or 0
    audit(request.user, "audit.export", "CSV")

    class Buffer:
        def write(self, value):
            return value

    def stream():
        writer = csv.writer(Buffer())
        yield "\ufeff" + writer.writerow(COLUMNS)
        for row in rows.filter(pk__lte=ceiling).values_list(*COLUMNS).iterator(chunk_size=1000):
            yield writer.writerow(map(protect, row))

    return StreamingHttpResponse(stream(), content_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="audit-trail.csv"'})
