from hashlib import sha256
from uuid import uuid4

from django.conf import settings
from django.core.files.storage import storages
from django.http import FileResponse

from backbone.access import audit
from backbone.http import Problem, endpoint, once
from backbone.models import Attachment
from .documents import reject_archived, require_draft_edit, scoped_document

MAX_SIZE = 10 * 1024 * 1024


def storage():
    return storages["default"]


def path(key):
    return f"documents/{key.hex}"


def serialize(row):
    return {"id": row.pk, "name": row.name, "size": row.size,
            "created_by": row.created_by, "created_at": row.created_at.isoformat()}


@endpoint("GET", "POST")
def attachments(request, kind, pk):
    user = request.user
    if request.method == "GET":
        scoped_document(user, kind, pk)
        rows = Attachment.objects.filter(kind=kind, object_id=pk).order_by("-id")
        return {"items": [serialize(row) for row in rows]}

    scoped_document(user, kind, pk)
    uploaded = request.FILES.get("file")
    if uploaded is None or not 0 < uploaded.size <= MAX_SIZE:
        raise Problem("invalid_attachment_size")
    name = uploaded.name.strip()
    if not name or len(name) > 160 or any(ord(char) < 32 for char in name):
        raise Problem("invalid_attachment_name")
    fingerprint = sha256()
    for chunk in uploaded.chunks():
        fingerprint.update(chunk)
    uploaded.seek(0)
    files = storage()
    written = None

    def create():
        nonlocal written
        current_document, current_row = scoped_document(user, kind, pk, lock=True)
        require_draft_edit(user, current_document, current_row)
        reject_archived(kind, current_row)
        key = uuid4()
        written = files.save(path(key), uploaded)
        if written != path(key):
            raise Problem("attachment_storage_conflict", 409)
        entry = Attachment.objects.create(kind=kind, object_id=pk, name=name, size=uploaded.size,
                                          storage_key=key, created_by=user.get_username())
        audit(user, "attachment.upload", f"{kind.upper()}:{pk} / #{entry.pk}")
        return serialize(entry)

    try:
        return once(request, "attachment.upload",
                    {"kind": kind, "id": pk, "name": name, "sha256": fingerprint.hexdigest()}, create)
    except Exception:
        if written:
            files.delete(written)
        raise


@endpoint("GET")
def download(request, kind, pk, attachment_id):
    scoped_document(request.user, kind, pk)
    row = Attachment.objects.filter(kind=kind, object_id=pk, pk=attachment_id).first()
    if row is None:
        raise Problem("not_found", 404)
    try:
        stream = storage().open(path(row.storage_key), "rb")
    except FileNotFoundError:
        raise Problem("attachment_missing", 404) from None
    response = FileResponse(stream, as_attachment=True, filename=row.name, content_type="application/octet-stream")
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response
