from backbone.access import visible
from backbone.registry import enabled_modules
from .models import Asset


def selectable_assets(user, site_id=None, *, restrict_site=False):
    assets = Asset.objects.order_by("tag")
    if "assets" not in enabled_modules():
        return assets.none()
    assets = visible(assets, user, "assets.view")
    return assets.filter(site_id=site_id) if restrict_site else assets


def reference(asset):
    return f"{asset.tag} · {asset.name}"
