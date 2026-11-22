from backbone.http import Problem, identifier
from .models import CostCenterScope, Reference


def center_for_site(value, site_id, *, required=False, legacy=False):
    key = identifier(value, "cost_center", optional=True)
    if key is None:
        if required:
            raise Problem("cost_center_required", 409)
        return None
    row = Reference.objects.filter(pk=key, ledger_id=1, kind="cost_center").select_related("center_scope").first()
    scope = getattr(row, "center_scope", None)
    if row is None or (scope is None and not legacy) or (scope and (scope.site_id != site_id or not scope.active)):
        raise Problem("cost_center_unavailable", 409)
    return row


def center_choices(site_id):
    return [{"id": row.reference_id, "name": f"{row.reference.code} · {row.reference.name}"}
            for row in CostCenterScope.objects.filter(site_id=site_id, active=True)
            .select_related("reference").order_by("reference__code")]
