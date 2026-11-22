from django.db import IntegrityError, transaction
from backbone.access import audit, has_permission, visible
from backbone.http import Problem, body, endpoint, identifier, once, require, site, string
from backbone.models import Site
from backbone.site_admin import require_active_site
from .models import Asset, AssetTransfer


def serialize(asset):
    return {"id": asset.pk, "name": asset.name, "tag": asset.tag, "site_id": asset.site_id}


@endpoint("GET", "POST", module="assets")
def assets(request):
    if request.method == "GET":
        require(request.user, "assets.view")
        rows = visible(Asset.objects.order_by("tag"), request.user, "assets.view")
        if request.GET.get('record','').isdigit():rows=rows.filter(pk=int(request.GET['record']))
        if "page_size" in request.GET:
            from backbone.services.pagination import operational_page
            return {**operational_page(request,rows,serialize,['name', 'tag', 'site__name'])}
        return {"items": [serialize(row) for row in rows]}
    data = body(request)
    fields = {"name": string(data, "name", 120), "tag": string(data, "tag", 60), "site_id": site(data)}
    require(request.user, "assets.create", fields["site_id"])

    def create():
        require_active_site(fields["site_id"], lock=True)
        try:
            with transaction.atomic():
                asset = Asset.objects.create(**fields)
        except IntegrityError:
            raise Problem("duplicate_tag", 409) from None
        audit(request.user, "asset.create", asset.pk)
        return serialize(asset)

    return once(request, "asset.create", fields, create)


def asset_for(user, pk, *, lock=False):
    rows = Asset.objects.select_for_update() if lock else Asset.objects.all()
    row = visible(rows, user, "assets.view").filter(pk=pk).first()
    if row is None:
        raise Problem("not_found", 404)
    return row


def check_revision(row, data):
    if row.revision != identifier(data.get("revision"), "revision"):
        raise Problem("revision_conflict", 409)


def transfer_rows(user,asset):
    from django.db.models import Q
    from backbone.access import scopes
    allowed = scopes(user, "assets.view")
    history = asset.transfers.order_by("-pk")
    if None not in allowed:
        history = history.filter(Q(source_site_id__in=allowed) | Q(destination_site_id__in=allowed))
    return history

@endpoint("GET",module="assets")
def transfer_page(request,pk):
    from backbone.services.pagination import paginate,search_rows
    rows=transfer_rows(request.user,asset_for(request.user,pk))
    rows=search_rows(rows,request.GET,["reason","source_site__name","destination_site__name"])
    return paginate(request,rows.values("source_site__name","destination_site__name","transferred_on","recorded_at","reason","actor_id"),lambda row:row)

def details(user, asset, include_history=True):
    from django.db.models import Q
    from backbone.access import scopes

    history = transfer_rows(user,asset)
    data = {**serialize(asset), "revision": asset.revision,
            "transfers": list((history if include_history else history.none()).values("source_site_id", "destination_site_id", "source_site__name",
                                            "destination_site__name", "transferred_on", "recorded_at", "reason", "actor_id"))}
    if has_permission(user, "assets.cost.view", asset.site_id):
        data["financial"] = {"purchased_on": asset.purchased_on, "purchase_cost": str(asset.purchase_cost)
            if asset.purchase_cost is not None else "", "purchase_currency": asset.purchase_currency,
            "purchase_reference": asset.purchase_reference}
    return data


@endpoint("GET", "PATCH", module="assets")
def detail(request, pk):
    user = request.user
    if request.method == "GET":
        return details(user, asset_for(user, pk),request.GET.get("history")!="paged")
    from accounting.views import day_field
    from purchasing.money import decimal_value
    from purchasing.views import CURRENCIES

    data = body(request)
    with transaction.atomic():
        row = asset_for(user, pk, lock=True)
        require(user, "assets.edit", row.site_id)
        check_revision(row, data)
        row.name = string(data, "name", 120)
        if "financial" in data:
            require(user, "assets.cost.view", row.site_id)
            require(user, "assets.cost.edit", row.site_id)
            values = data["financial"]
            if not isinstance(values, dict):
                raise Problem("invalid_asset_cost")
            row.purchased_on = day_field(values, "purchased_on") if values.get("purchased_on") else None
            cost = string(values, "purchase_cost", 20, required=False)
            row.purchase_cost = decimal_value(cost, 15, 2, "purchase_cost") if cost else None
            row.purchase_currency = string(values, "purchase_currency", 3, required=False).upper()
            if (cost and row.purchase_currency not in CURRENCIES) or (not cost and row.purchase_currency):
                raise Problem("invalid_currency")
            row.purchase_reference = string(values, "purchase_reference", 120, required=False)
        row.revision += 1
        row.save()
        audit(user, "asset.update", row.pk)
        return details(user, row)


@endpoint("POST", module="assets")
def transfer(request, pk):
    from accounting.views import day_field
    from django.utils import timezone
    from workorders.models import MaintenanceJob

    user = request.user
    data = body(request)
    current = asset_for(user, pk)
    destination = identifier(data.get("destination_site_id"), "site")
    day = day_field(data, "transferred_on")
    reason = string(data, "reason", 500)
    require(user, "assets.transfer", destination)
    require(user, "assets.view", destination)

    def create():
        site_ids = {destination, current.site_id} - {None}
        list(Site.objects.select_for_update().filter(pk__in=site_ids).order_by("pk"))
        row = asset_for(user, pk, lock=True)
        check_revision(row, data)
        if row.site_id not in (*site_ids, None):
            raise Problem("revision_conflict", 409)
        require(user, "assets.transfer", row.site_id)
        require_active_site(destination)
        if destination == row.site_id or day > timezone.localdate() or row.transfers.filter(transferred_on__gt=day).exists():
            raise Problem("invalid_asset_transfer", 409)
        if MaintenanceJob.objects.filter(asset=row).exclude(stage__in=("closed", "cancelled")).exists():
            raise Problem("asset_has_open_maintenance", 409)
        from .models import AssetProfile

        if AssetProfile.objects.filter(asset=row, parent__isnull=False).exists() or row.maintenance_children.exists():
            raise Problem("asset_transfer_hierarchy", 409)
        movement = AssetTransfer.objects.create(asset=row, source_site_id=row.site_id,
            destination_site_id=destination, transferred_on=day, reason=reason, actor=user)
        row.site_id = destination
        row.revision += 1
        row.save(update_fields=["site", "revision"])
        audit(user, "asset.transfer", f"#{row.pk} / {movement.source_site_id} → {destination} / {day} / {reason}")
        return {"id": row.pk, "transfer_id": movement.pk, "site_id": destination, "revision": row.revision}

    return once(request, "asset.transfer", {"asset": pk, **data}, create)
