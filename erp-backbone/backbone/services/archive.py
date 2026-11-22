from django.db import transaction

from backbone.access import audit
from backbone.http import Problem, body, endpoint, identifier
from .documents import archive_rows, require_draft_edit, scoped_document


@endpoint("PATCH")
def archive(request, kind, pk):
    data = body(request)
    desired = data.get("archived")
    if type(desired) is not bool:
        raise Problem("invalid_archived")
    user = request.user
    with transaction.atomic():
        document, row = scoped_document(user, kind, pk, lock=True)
        require_draft_edit(user, document, row)
        if row.revision != identifier(data.get("revision"), "revision"):
            raise Problem("revision_conflict", 409)
        entries = archive_rows(kind).filter(object_id=pk)
        if entries.exists() != desired:
            if desired:
                entries.create(kind=kind, object_id=pk, archived_by=user.get_username())
            else:
                from backbone.site_admin import require_active_site

                require_active_site(row.site_id, lock=True)
                entries.delete()
            row.revision += 1
            row.save(update_fields=["revision"])
            audit(user, "document.archive" if desired else "document.restore", f"{kind.upper()}:{pk}")
        return {"archived": desired, "revision": row.revision}
