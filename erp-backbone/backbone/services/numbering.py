import re

from django.db import transaction

from backbone.access import audit
from backbone.http import Problem, body, endpoint, identifier
from backbone.models import NumberingCounter, NumberingRule, Site
from .documents import catalog, definition

LIMIT = 999_999_999_999


def current_rule(kind):
    document = definition(kind)
    rule, _ = NumberingRule.objects.get_or_create(
        kind=kind, defaults={"prefix": document.default_prefix, "start": document.minimum_start},
    )
    return rule


def candidate(kind, prefix, per_site, start, scope, last):
    document = definition(kind)
    number = max(start, last + 1)
    if kind == "pr" and not per_site and prefix == document.default_prefix:
        number = max(number, document.minimum_start())
    parts = [prefix] if prefix else []
    if per_site:
        parts.append(f"S{scope}")
    while number <= LIMIT:
        value = "-".join([*parts, f"{number:06d}"])
        if not document.model.objects.filter(number=value).exists():
            return number, value
        number += 1
    raise Problem("numbering_exhausted", 409)


def allocate_number(kind, site_id):
    with transaction.atomic():
        rule = NumberingRule.objects.select_for_update().get(pk=current_rule(kind).pk)
        if rule.per_site and site_id is None:
            raise Problem("invalid_site")
        scope = site_id if rule.per_site else 0
        counter, _ = NumberingCounter.objects.get_or_create(rule=rule, scope=scope, defaults={"last": rule.start - 1})
        counter.last, value = candidate(kind, rule.prefix, rule.per_site, rule.start, scope, counter.last)
        counter.save(update_fields=["last"])
        return value


def serialize(rule):
    return {"kind": rule.kind, "prefix": rule.prefix, "per_site": rule.per_site,
            "allow_site_numbering": definition(rule.kind).allow_site_numbering, "start": rule.start,
            "revision": rule.revision, "locked": rule.counters.exists()}


def options(data, kind):
    prefix, per_site = data.get("prefix"), data.get("per_site")
    start = identifier(data.get("start"), "start")
    if (not isinstance(prefix, str) or not re.fullmatch(r"[A-Z0-9]{0,16}", prefix)
            or type(per_site) is not bool or start > LIMIT):
        raise Problem("invalid_numbering_rule")
    document = definition(kind)
    if per_site and not document.allow_site_numbering:
        raise Problem("company_numbering_required")
    if prefix == document.default_prefix and not per_site and start < document.minimum_start():
        raise Problem("numbering_legacy_conflict", 409)
    return prefix, per_site, start


@endpoint("GET", admin=True)
def rules(request):
    return {"items": [serialize(current_rule(kind)) for kind in catalog()]}


@endpoint("PATCH", admin=True)
def rule(request, kind):
    data = body(request)
    prefix, per_site, start = options(data, kind)
    with transaction.atomic():
        row = NumberingRule.objects.select_for_update().get(pk=current_rule(kind).pk)
        if row.revision != identifier(data.get("revision"), "revision"):
            raise Problem("revision_conflict", 409)
        if row.counters.exists() and data.get("apply_to_future") is not True:
            raise Problem("numbering_locked", 409)
        row.prefix, row.per_site, row.start = prefix, per_site, start
        row.revision += 1
        row.save(update_fields=["prefix", "per_site", "start", "revision"])
        audit(request.user, "numbering.configure", kind.upper())
        return serialize(row)


@endpoint("POST", admin=True)
def preview(request, kind):
    data = body(request)
    prefix, per_site, start = options(data, kind)
    scope = 0
    if per_site:
        scope = identifier(data.get("site_id"), "site")
        if not Site.objects.filter(pk=scope).exists():
            raise Problem("invalid_site")
    last = NumberingCounter.objects.filter(rule__kind=kind, scope=scope).values_list("last", flat=True).first()
    return {"number": candidate(kind, prefix, per_site, start, scope, last or 0)[1]}
