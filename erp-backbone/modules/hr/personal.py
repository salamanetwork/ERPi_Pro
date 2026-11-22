import base64
import hashlib

from cryptography.fernet import Fernet
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.views.decorators.cache import never_cache

from accounting.views import day_field
from backbone.access import audit, has_permission
from backbone.http import Problem, body, endpoint, identifier
from .access import one
from .models import Employee


def identity_cipher():
    material = b"erp-backbone/hr-identity/v1\0" + settings.ERP_MFA_KEY.encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(material).digest()))


def personal_data(user, employee, *, reveal_id=False):
    own = employee.user_id == user.pk and has_permission(user, "hr.self", employee.site_id)
    edit = has_permission(user, "hr.manage", employee.site_id)
    reveal = has_permission(user, "hr.identity.view_full", employee.site_id)
    if not (own or edit or reveal):
        raise Problem("not_found", 404)
    data = {
        "revision": employee.revision, "can_edit": edit, "can_view_full_id": reveal,
        "birth_date": employee.birth_date.isoformat() if employee.birth_date else None,
        "nationality": employee.nationality, "address": employee.address,
        "national_id_masked": employee.national_id_masked,
    }
    if reveal and reveal_id:
        data["national_id"] = (identity_cipher().decrypt(employee.national_id_encrypted.encode()).decode()
                               if employee.national_id_encrypted else "")
    return data


def bounded_text(data, field, size):
    value = data.get(field)
    if not isinstance(value, str) or len(value.strip()) > size or any(ord(ch) < 32 and ch not in "\n\r\t" for ch in value):
        raise Problem("invalid_" + field)
    return value.strip()


@never_cache
@endpoint("GET", "PATCH", module="hr")
def employee_personal(request, pk):
    user = request.user
    if request.method == "GET":
        return personal_data(user, one(Employee.objects.all(), pk), reveal_id=request.GET.get("reveal") == "1")

    data = body(request)
    allowed = {"revision", "birth_date", "nationality", "address", "national_id"}
    if data.keys() - allowed or not (data.keys() - {"revision"}):
        raise Problem("invalid_personal_details")
    with transaction.atomic():
        row = one(Employee.objects.select_for_update(), pk)
        if not has_permission(user, "hr.manage", row.site_id):
            raise Problem("not_found", 404)
        if identifier(data.get("revision"), "revision") != row.revision:
            raise Problem("revision_conflict", 409)
        if "birth_date" in data:
            birth = None if data["birth_date"] is None else day_field(data, "birth_date")
            if birth and (birth > timezone.localdate() or birth > row.joined):
                raise Problem("invalid_birth_date")
            row.birth_date = birth
        if "nationality" in data:
            row.nationality = bounded_text(data, "nationality", 80)
        if "address" in data:
            row.address = bounded_text(data, "address", 500)
        if "national_id" in data:
            value = bounded_text(data, "national_id", 64)
            if any(not (ch.isalnum() or ch in " -./") for ch in value) or (value and not any(ch.isalnum() for ch in value)):
                raise Problem("invalid_national_id")
            row.national_id_encrypted = identity_cipher().encrypt(value.encode()).decode() if value else ""
            suffix = "".join(c for c in value if c.isalnum())
            row.national_id_masked = ("••••" + suffix[-4:] if len(suffix) > 4 else "••••") if value else ""
        row.revision += 1
        row.save(update_fields=["birth_date", "nationality", "address", "national_id_encrypted",
                                "national_id_masked", "revision"])
        audit(user, "hr.employee.personal.update", row.pk)
        return personal_data(user, row)
