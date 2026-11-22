from django.apps import apps
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.views.decorators.cache import never_cache

from backbone.access import has_permission, preload_scopes, scopes, visible
from backbone.http import endpoint
from backbone.models import Assignment, Site
from backbone.registry import enabled_modules
from backbone.validation import Problem


def _rank(row: dict, q_low: str):
    if not q_low:
        return (2, row["code"], row["name"])
    code = (row["code"] or "").lower()
    name = (row["name"] or "").lower()
    if code == q_low or str(row["id"]) == q_low:
        return (0, code, name)
    if code.startswith(q_low) or name.startswith(q_low):
        return (1, code, name)
    return (2, code, name)


def _format(item_id: int, code: str = "", name: str = "", subtitle: str = "", meta: dict | None = None) -> dict:
    code = (code or "").strip()
    name = (name or "").strip()
    subtitle = (subtitle or "").strip()
    label = f"{code} · {name}" if code and name and code != name else (name or code or f"#{item_id}")
    payload = {
        "id": item_id,
        "code": code,
        "name": name or code or f"#{item_id}",
        "subtitle": subtitle,
        "label": label,
    }
    if meta:
        payload["meta"] = meta
    return payload


def _parse_int(raw: str | None) -> int | None:
    if not raw:
        return None
    raw = raw.strip()
    return int(raw) if raw.isdigit() else None


@never_cache
@endpoint("GET")
def search(request, kind: str):
    user = request.user
    preload_scopes(user)
    enabled = enabled_modules()

    q = (request.GET.get("q") or "").strip()[:120]
    q_low = q.lower()
    exact_id = _parse_int(request.GET.get("id"))
    site_id = _parse_int(request.GET.get("site_id"))
    permission_hint = (request.GET.get("permission") or "").strip()
    include_inactive = request.GET.get("include_inactive") == "1"
    try:
        limit = min(50, max(1, int(request.GET.get("limit") or 20)))
    except ValueError:
        limit = 20

    rows: list[dict] = []

    if kind == "users":
        User = get_user_model()
        qs = User.objects.all()
        if not include_inactive and exact_id is None:
            qs = qs.filter(is_active=True)
        if site_id is not None and not user.is_superuser:
            user_ids = Assignment.objects.filter(Q(site_id=site_id) | Q(site_id__isnull=True)).values_list("user_id", flat=True)
            qs = qs.filter(Q(id__in=user_ids) | Q(is_superuser=True))
        if exact_id is not None:
            qs = qs.filter(pk=exact_id)
        elif q:
            cond = (
                Q(username__icontains=q)
                | Q(first_name__icontains=q)
                | Q(last_name__icontains=q)
                | Q(email__icontains=q)
            )
            if q.isdigit():
                cond |= Q(pk=int(q))
            qs = qs.filter(cond)
        users = list(qs.order_by("username")[: limit * 3])
        emp_map = {}
        if "hr" in enabled and users:
            Employee = apps.get_model("hr", "Employee")
            for emp in Employee.objects.filter(user_id__in=[u.id for u in users]).only("user_id", "number", "name"):
                emp_map[emp.user_id] = emp
        for u in users:
            full = f"{u.first_name} {u.last_name}".strip()
            emp = emp_map.get(u.id)
            display = full or (emp.name if emp else "") or u.username
            sub_parts = [p for p in (emp.number if emp else "", u.email or "") if p]
            rows.append(_format(u.id, code=u.username, name=display, subtitle=" · ".join(sub_parts)))

    elif kind == "sites":
        qs = Site.objects.all()
        if not include_inactive and exact_id is None:
            qs = qs.filter(active=True)
        if permission_hint:
            allowed = scopes(user, permission_hint)
            if None not in allowed:
                qs = qs.filter(pk__in=allowed)
        elif not user.is_superuser:
            assigned = {row.site_id for row in Assignment.objects.filter(user=user)}
            if None not in assigned:
                qs = qs.filter(pk__in=assigned)
        if exact_id is not None:
            qs = qs.filter(pk=exact_id)
        elif q:
            cond = Q(name__icontains=q) | Q(description__icontains=q)
            if q.isdigit():
                cond |= Q(pk=int(q))
            qs = qs.filter(cond)
        for s in qs.order_by("name")[: limit * 2]:
            rows.append(_format(s.id, code=f"S-{s.id}", name=s.name, subtitle=s.description))

    elif kind == "employees":
        if "hr" not in enabled:
            return {"results": []}
        Employee = apps.get_model("hr", "Employee")
        qs = Employee.objects.select_related("site")
        if not include_inactive and exact_id is None:
            qs = qs.filter(archived_at__isnull=True, ended__isnull=True)
        allowed = scopes(user, "hr.view")
        if not allowed:
            qs = qs.filter(user=user)
        elif None not in allowed:
            qs = qs.filter(Q(site_id__in=allowed) | Q(user=user))
        if site_id is not None:
            qs = qs.filter(site_id=site_id)
        if exact_id is not None:
            qs = qs.filter(pk=exact_id)
        elif q:
            cond = Q(number__icontains=q) | Q(name__icontains=q) | Q(department__icontains=q) | Q(title__icontains=q)
            if q.isdigit():
                cond |= Q(pk=int(q))
            qs = qs.filter(cond)
        for emp in qs.order_by("number", "name")[: limit * 3]:
            sub = " · ".join(p for p in (emp.site.name if emp.site_id else "", emp.department, emp.title) if p)
            rows.append(_format(emp.id, code=emp.number, name=emp.name, subtitle=sub, meta={"site_id": emp.site_id}))

    elif kind == "customers":
        if "sales" not in enabled and "crm" not in enabled:
            return {"results": []}
        if not (has_permission(user, "sales.view") or has_permission(user, "crm.view")):
            raise Problem("forbidden", 403)
        Customer = apps.get_model("sales", "Customer")
        qs = Customer.objects.all()
        if not include_inactive and exact_id is None:
            qs = qs.filter(active=True)
        if exact_id is not None:
            qs = qs.filter(pk=exact_id)
        elif q:
            cond = Q(code__icontains=q) | Q(name__icontains=q) | Q(legal_name__icontains=q) | Q(tax_id__icontains=q)
            if q.isdigit():
                cond |= Q(pk=int(q))
            qs = qs.filter(cond)
        for c in qs.order_by("code", "name")[: limit * 3]:
            sub = " · ".join(p for p in (c.legal_name if c.legal_name != c.name else "", c.tax_id) if p)
            rows.append(_format(c.id, code=c.code, name=c.name, subtitle=sub))

    elif kind == "suppliers":
        if "purchasing" not in enabled and "ap" not in enabled:
            return {"results": []}
        if not (has_permission(user, "purchasing.view") or has_permission(user, "ap.view")):
            raise Problem("forbidden", 403)
        Supplier = apps.get_model("purchasing", "Supplier")
        qs = Supplier.objects.all()
        if not include_inactive and exact_id is None:
            qs = qs.filter(active=True)
        if exact_id is not None:
            qs = qs.filter(pk=exact_id)
        elif q:
            cond = Q(code__icontains=q) | Q(name__icontains=q) | Q(legal_name__icontains=q) | Q(tax_id__icontains=q)
            if q.isdigit():
                cond |= Q(pk=int(q))
            qs = qs.filter(cond)
        for s in qs.order_by("code", "name")[: limit * 3]:
            sub = " · ".join(p for p in (s.currency_default, s.tax_id) if p)
            rows.append(_format(s.id, code=s.code, name=s.name, subtitle=sub, meta={"currency": s.currency_default}))

    elif kind == "parties":
        if "parties" not in enabled:
            return {"results": []}
        Party = apps.get_model("parties", "Party")
        qs = visible(Party.objects.all(), user, "parties.view")
        if not include_inactive and exact_id is None:
            qs = qs.filter(active=True)
        if site_id is not None:
            qs = qs.filter(Q(site_id=site_id) | Q(site_id__isnull=True))
        if exact_id is not None:
            qs = qs.filter(pk=exact_id)
        elif q:
            cond = (
                Q(name_en__icontains=q)
                | Q(name_ar__icontains=q)
                | Q(phone__icontains=q)
                | Q(email__icontains=q)
                | Q(identity_number__icontains=q)
            )
            if q.isdigit():
                cond |= Q(pk=int(q))
            qs = qs.filter(cond)
        for p in qs.order_by("id")[: limit * 3]:
            primary = p.name_ar or p.name_en
            secondary = p.name_en if p.name_ar and p.name_en != p.name_ar else ""
            sub = " · ".join(x for x in (secondary, p.phone, p.email) if x)
            rows.append(_format(p.id, code=p.identity_number or f"P-{p.id}", name=primary, subtitle=sub))

    elif kind == "items":
        if "inventory" not in enabled:
            return {"results": []}
        Item = apps.get_model("inventory", "Item")
        qs = Item.objects.select_related("base_unit")
        if not include_inactive and exact_id is None:
            qs = qs.filter(active=True)
        if exact_id is not None:
            qs = qs.filter(pk=exact_id)
        elif q:
            cond = Q(code__icontains=q) | Q(name__icontains=q)
            if q.isdigit():
                cond |= Q(pk=int(q))
            qs = qs.filter(cond)
        for item in qs.order_by("code", "name")[: limit * 3]:
            unit_code = item.base_unit.code if item.base_unit_id else ""
            rows.append(_format(
                item.id,
                code=item.code,
                name=item.name,
                subtitle=unit_code,
                meta={"base_unit_id": item.base_unit_id, "unit_code": unit_code},
            ))

    elif kind == "warehouses":
        if "inventory" not in enabled:
            return {"results": []}
        Warehouse = apps.get_model("inventory", "Warehouse")
        qs = visible(Warehouse.objects.select_related("site"), user, "inventory.view")
        if not include_inactive and exact_id is None:
            qs = qs.filter(active=True)
        if site_id is not None:
            qs = qs.filter(site_id=site_id)
        if exact_id is not None:
            qs = qs.filter(pk=exact_id)
        elif q:
            cond = Q(code__icontains=q) | Q(name__icontains=q)
            if q.isdigit():
                cond |= Q(pk=int(q))
            qs = qs.filter(cond)
        for w in qs.order_by("code", "name")[: limit * 2]:
            rows.append(_format(w.id, code=w.code, name=w.name, subtitle=w.site.name if w.site_id else ""))

    elif kind == "locations":
        if "inventory" not in enabled:
            return {"results": []}
        Location = apps.get_model("inventory", "Location")
        qs = Location.objects.select_related("warehouse", "warehouse__site")
        allowed = scopes(user, "inventory.view")
        if None not in allowed:
            qs = qs.filter(warehouse__site_id__in=allowed)
        if site_id is not None:
            qs = qs.filter(warehouse__site_id=site_id)
        wh_id = _parse_int(request.GET.get("warehouse_id"))
        if wh_id is not None:
            qs = qs.filter(warehouse_id=wh_id)
        if exact_id is not None:
            qs = qs.filter(pk=exact_id)
        elif q:
            cond = Q(code__icontains=q) | Q(name__icontains=q) | Q(warehouse__name__icontains=q) | Q(warehouse__code__icontains=q)
            if q.isdigit():
                cond |= Q(pk=int(q))
            qs = qs.filter(cond)
        for loc in qs.order_by("warehouse__code", "code")[: limit * 2]:
            rows.append(_format(loc.id, code=loc.code, name=f"{loc.warehouse.name} / {loc.name}", subtitle=loc.warehouse.code))

    elif kind == "accounts":
        if "accounting" not in enabled:
            return {"results": []}
        Account = apps.get_model("accounting", "Account")
        qs = Account.objects.all()
        if not include_inactive and exact_id is None:
            qs = qs.filter(active=True)
        if request.GET.get("detail_only") == "1":
            qs = qs.filter(detail=True)
        if exact_id is not None:
            qs = qs.filter(pk=exact_id)
        elif q:
            cond = Q(code__icontains=q) | Q(name__icontains=q)
            if q.isdigit():
                cond |= Q(pk=int(q))
            qs = qs.filter(cond)
        for acc in qs.order_by("code")[: limit * 3]:
            rows.append(_format(acc.id, code=acc.code, name=acc.name, subtitle=acc.kind))

    elif kind == "cost_centers":
        if "accounting" not in enabled:
            return {"results": []}
        Reference = apps.get_model("accounting", "Reference")
        qs = Reference.objects.filter(kind="cost_center")
        if site_id is not None:
            CostCenterScope = apps.get_model("accounting", "CostCenterScope")
            scoped_ids = CostCenterScope.objects.filter(site_id=site_id, active=True).values_list("reference_id", flat=True)
            qs = qs.filter(pk__in=scoped_ids)
        if exact_id is not None:
            qs = qs.filter(pk=exact_id)
        elif q:
            cond = Q(code__icontains=q) | Q(name__icontains=q)
            if q.isdigit():
                cond |= Q(pk=int(q))
            qs = qs.filter(cond)
        for ref in qs.order_by("code", "name")[: limit * 2]:
            rows.append(_format(ref.id, code=ref.code, name=ref.name))

    elif kind == "assets":
        if "assets" not in enabled:
            return {"results": []}
        Asset = apps.get_model("assets", "Asset")
        qs = visible(Asset.objects.select_related("site"), user, "assets.view")
        if site_id is not None:
            qs = qs.filter(site_id=site_id)
        if exact_id is not None:
            qs = qs.filter(pk=exact_id)
        elif q:
            cond = Q(tag__icontains=q) | Q(name__icontains=q) | Q(purchase_reference__icontains=q)
            if q.isdigit():
                cond |= Q(pk=int(q))
            qs = qs.filter(cond)
        for asset in qs.order_by("tag", "name")[: limit * 2]:
            rows.append(_format(asset.id, code=asset.tag, name=asset.name, subtitle=asset.site.name if asset.site_id else ""))

    elif kind == "projects":
        if "projects" not in enabled:
            return {"results": []}
        Project = apps.get_model("projects", "Project")
        qs = visible(Project.objects.select_related("site"), user, "projects.view")
        if site_id is not None:
            qs = qs.filter(site_id=site_id)
        if exact_id is not None:
            qs = qs.filter(pk=exact_id)
        elif q:
            cond = Q(code__icontains=q) | Q(name__icontains=q)
            if q.isdigit():
                cond |= Q(pk=int(q))
            qs = qs.filter(cond)
        for proj in qs.order_by("code", "name")[: limit * 2]:
            rows.append(_format(proj.id, code=proj.code, name=proj.name, subtitle=proj.site.name if proj.site_id else ""))

    else:
        raise Problem("unknown_lookup_kind", 404)

    rows.sort(key=lambda r: _rank(r, q_low))
    return {"results": rows[:limit]}
