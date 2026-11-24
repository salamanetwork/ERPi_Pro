from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.utils import timezone

from .access import audit
from .http import Problem
from .models import CompanyProfile

VALID_DELETION_POLICIES = {"soft", "permanent"}


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
