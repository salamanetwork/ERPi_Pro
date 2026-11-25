from django.apps import apps
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.utils import timezone

from .access import audit
from .http import Problem
from .models import CompanyProfile

VALID_DELETION_POLICIES = {"soft", "permanent"}

# Registry mapping kind -> (app_label, model_name, module_id, code_field, name_field)
DELETION_REGISTRY = {
    "warehouse": ("inventory", "Warehouse", "inventory", "code", "name"),
    "location": ("inventory", "Location", "inventory", "code", "name"),
    "unit": ("inventory", "Unit", "inventory", "code", "name"),
    "item": ("inventory", "Item", "inventory", "code", "name"),
    "customer": ("sales", "Customer", "customers", "code", "name"),
    "supplier": ("purchasing", "Supplier", "suppliers", "code", "name"),
    "hr-reference": ("hr", "HRReference", "hr", "code", "name"),
}


def get_deletion_policy() -> str:
    row = CompanyProfile.objects.values("deletion_policy").filter(pk=1).first()
    pol = (row or {}).get("deletion_policy") or "soft"
    return pol if pol in VALID_DELETION_POLICIES else "soft"


def perform_record_deletion(instance, user, audit_action: str, has_dependencies_fn=None) -> dict:
    """Delete or soft-delete a master record according to CompanyProfile.deletion_policy."""
    if has_dependencies_fn is not None and has_dependencies_fn(instance):
        raise Problem("record_has_dependencies", 409)

    policy = get_deletion_policy()
    pk = instance.pk

    try:
        with transaction.atomic():
            if policy == "permanent":
                instance.delete()
                audit(user, f"{audit_action}.permanent", pk)
                return {"id": pk, "deleted": True, "mode": "permanent"}

            update_fields = []
            if hasattr(instance, "is_deleted"):
                instance.is_deleted = True
                update_fields.append("is_deleted")
            if hasattr(instance, "deleted_at"):
                instance.deleted_at = timezone.now()
                update_fields.append("deleted_at")
            if hasattr(instance, "active"):
                instance.active = False
                update_fields.append("active")
            if update_fields:
                instance.save(update_fields=update_fields)
            else:
                instance.delete()
                policy = "permanent"
            audit(user, f"{audit_action}.{policy}", pk)
            return {"id": pk, "deleted": True, "mode": policy}
    except ProtectedError:
        raise Problem("record_has_dependencies", 409)


def _resolve_deleted_by_map(records: list[dict]) -> dict[tuple[str, str], str]:
    """Resolve who soft-deleted each record from AuditEvent logs."""
    if not records:
        return {}
    from django.contrib.auth import get_user_model
    from .models import AuditEvent, Profile

    pks = {str(r["id"]) for r in records}
    events = (
        AuditEvent.objects.filter(action__endswith=".delete.soft", target__in=pks)
        .order_by("-occurred_at", "-id")
        .values("actor", "actor_user_id", "action", "target")
    )
    user_ids = {e["actor_user_id"] for e in events if e["actor_user_id"] is not None}
    users = get_user_model().objects.in_bulk(user_ids)
    profiles = Profile.objects.in_bulk(user_ids, field_name="user")

    # Map audit action prefix -> DELETION_REGISTRY kind
    prefix_to_kind = {
        "inventory.item": "item",
        "sales.customer": "customer",
        "purchasing.supplier": "supplier",
        "inventory.warehouse": "warehouse",
        "inventory.location": "location",
        "inventory.unit": "unit",
        "hr.reference": "hr-reference",
    }

    resolved: dict[tuple[str, str], str] = {}
    for ev in events:
        action = ev["action"] or ""
        base_action = action[: -len(".delete.soft")] if action.endswith(".delete.soft") else action
        kind = prefix_to_kind.get(base_action)
        if not kind:
            continue
        key = (kind, str(ev["target"]))
        if key in resolved:
            continue
        uid = ev["actor_user_id"]
        if uid in users:
            u = users[uid]
            prof = profiles.get(uid)
            resolved[key] = (
                (prof.iam_display_name if prof and prof.iam_display_name else "")
                or u.get_full_name()
                or u.username
                or ev["actor"]
                or ""
            )
        else:
            resolved[key] = ev["actor"] or ""
    return resolved


def list_deleted_records(kind_filter: str | None = None) -> list[dict]:
    """Return all soft-deleted records across registered ERP modules."""
    results = []
    kinds = [kind_filter] if kind_filter and kind_filter in DELETION_REGISTRY else list(DELETION_REGISTRY.keys())
    for kind in kinds:
        app_label, model_name, module_id, code_field, name_field = DELETION_REGISTRY[kind]
        Model = apps.get_model(app_label, model_name)
        if not hasattr(Model, "is_deleted"):
            continue
        qs = Model.objects.filter(is_deleted=True).order_by("-deleted_at", "-id")
        for obj in qs:
            results.append({
                "kind": kind,
                "module": module_id,
                "id": obj.pk,
                "code": getattr(obj, code_field, "") or "",
                "name": getattr(obj, name_field, "") or "",
                "deleted_at": obj.deleted_at.isoformat() if getattr(obj, "deleted_at", None) else None,
            })
    deleted_by_map = _resolve_deleted_by_map(results)
    for item in results:
        item["deleted_by"] = deleted_by_map.get((item["kind"], str(item["id"])), "")
    results.sort(key=lambda r: r["deleted_at"] or "", reverse=True)
    return results


def restore_deleted_record(kind: str, pk: int, user) -> dict:
    """Restore a soft-deleted record by kind and primary key."""
    if kind not in DELETION_REGISTRY:
        raise Problem("invalid_trash_kind", 400)
    app_label, model_name, module_id, code_field, name_field = DELETION_REGISTRY[kind]
    Model = apps.get_model(app_label, model_name)
    try:
        with transaction.atomic():
            obj = Model.objects.select_for_update().filter(pk=pk, is_deleted=True).first()
            if obj is None:
                raise Problem("not_found", 404)
            update_fields = ["is_deleted"]
            obj.is_deleted = False
            if hasattr(obj, "deleted_at"):
                obj.deleted_at = None
                update_fields.append("deleted_at")
            if hasattr(obj, "active"):
                obj.active = True
                update_fields.append("active")
            obj.save(update_fields=update_fields)
            audit(user, f"trash.restore.{kind}", pk)
            return {
                "restored": True,
                "kind": kind,
                "module": module_id,
                "id": obj.pk,
                "code": getattr(obj, code_field, "") or "",
                "name": getattr(obj, name_field, "") or "",
            }
    except IntegrityError:
        raise Problem("restore_conflict", 409)


def purge_deleted_record(kind: str, pk: int, user) -> dict:
    """Permanently delete a single soft-deleted record from the database."""
    if kind not in DELETION_REGISTRY:
        raise Problem("invalid_trash_kind", 400)
    app_label, model_name, module_id, code_field, name_field = DELETION_REGISTRY[kind]
    Model = apps.get_model(app_label, model_name)
    try:
        with transaction.atomic():
            obj = Model.objects.select_for_update().filter(pk=pk, is_deleted=True).first()
            if obj is None:
                raise Problem("not_found", 404)
            code_val = getattr(obj, code_field, "") or ""
            name_val = getattr(obj, name_field, "") or ""
            obj.delete()
            audit(user, f"trash.purge.{kind}", pk)
            return {
                "purged": True,
                "kind": kind,
                "module": module_id,
                "id": pk,
                "code": code_val,
                "name": name_val,
            }
    except ProtectedError:
        raise Problem("record_has_dependencies", 409)


def purge_all_deleted_records(user, kind_filter: str | None = None, items: list[dict] | None = None) -> dict:
    """Permanently delete all (or targeted) soft-deleted records, skipping protected ones."""
    purged_count = 0
    skipped_count = 0
    targets: list[tuple[str, int]] = []

    if items:
        for entry in items:
            k = entry.get("kind")
            raw_id = entry.get("id")
            if k in DELETION_REGISTRY and raw_id is not None:
                targets.append((k, int(raw_id)))
    else:
        kinds = [kind_filter] if kind_filter and kind_filter in DELETION_REGISTRY else list(DELETION_REGISTRY.keys())
        for kind in kinds:
            app_label, model_name, *_ = DELETION_REGISTRY[kind]
            Model = apps.get_model(app_label, model_name)
            if not hasattr(Model, "is_deleted"):
                continue
            for pk in Model.objects.filter(is_deleted=True).values_list("pk", flat=True):
                targets.append((kind, int(pk)))

    for kind, pk in targets:
        app_label, model_name, *_ = DELETION_REGISTRY[kind]
        Model = apps.get_model(app_label, model_name)
        try:
            with transaction.atomic():
                obj = Model.objects.select_for_update().filter(pk=pk, is_deleted=True).first()
                if obj is None:
                    continue
                obj.delete()
                audit(user, f"trash.purge.{kind}", pk)
                purged_count += 1
        except ProtectedError:
            skipped_count += 1

    return {
        "purged": purged_count,
        "skipped": skipped_count,
    }
