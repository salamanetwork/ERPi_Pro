from django.db import transaction
from django.utils import timezone
from backbone.http import endpoint,require,body
from backbone.validation import Problem
from publication.views import scoped
from .models import ExternalIdentity,ExternalSession
from .services import event

def serialize(row):
    return {"id":str(row.pk),"display_name":row.display_name,"email":row.normalized_email,"status":row.status,"revision":row.revision}

@endpoint("GET","PATCH",module="public_identity")
def identities(request,pk=None):
    permission="public_identity.view" if request.method=="GET" else "public_identity.manage"
    require(request.user,permission)
    rows=ExternalIdentity.objects.filter(externalmembership__space__in=scoped(request.user,permission)).distinct()
    if request.method=="GET":
        try: site=int(request.GET.get("space_id",0));page=max(1,int(request.GET.get("page",1)))
        except ValueError: raise Problem("invalid_space_id",field="space_id")
        rows=rows.filter(externalmembership__space_id=site,externalmembership__space__in=scoped(request.user,permission)).order_by("created_at");count=rows.count();start=(page-1)*25
        if request.GET.get("status")=="active":
            rows=rows.filter(status="active",externalmembership__space_id=site,externalmembership__active=True)
        if "page_size" in request.GET or "q" in request.GET:
            from backbone.services.pagination import paginate,search_rows
            return paginate(request,search_rows(rows,request.GET,['normalized_email', 'display_name']),serialize)
        return {"count":count,"next":f"?space_id={site}&page={page+1}" if start+25<count else None,"previous":f"?space_id={site}&page={page-1}" if page>1 else None,"results":[serialize(r) for r in rows[start:start+25]]}
    data=body(request)
    with transaction.atomic():
        # Global identity state can affect memberships outside the operator's site scope.
        # Only all-site managers may disable/re-enable a global identity.
        require(request.user,permission,None)
        row=ExternalIdentity.objects.select_for_update().filter(pk=pk).first()
        if not row: raise Problem("not_found",404)
        if data.get("revision")!=row.revision: raise Problem("revision_conflict",409)
        if data.get("status") not in {"active","disabled"} or data.get("status")=="active" and not row.verified_at:
            raise Problem("invalid_status",field="status")
        row.status=data["status"];row.auth_version+=1;row.revision+=1;row.save()
        ExternalSession.objects.filter(identity=row,revoked_at=None).update(revoked_at=timezone.now())
        event(row,"status_changed",row.status,request.user)
        return serialize(row)

@endpoint("GET","POST",admin=True,module="public_identity")
@transaction.atomic
def privacy(request,pk,operation):
    from django.contrib.auth.hashers import make_password
    from django.http import JsonResponse
    from media_service.models import MediaAsset
    if request.method!=("GET" if operation=="export" else "POST"):raise Problem("method_not_allowed",405)
    row=ExternalIdentity.objects.select_for_update().filter(pk=pk).first()
    if not row:raise Problem("not_found",404)
    if operation=="export":
        result={"profile":serialize(row),"files":list(MediaAsset.objects.filter(external_owner=row,deleted_at=None).values("id","space_id","original_name","size","mime","created_at"))}
    else:
        if body(request).get("revision")!=row.revision:raise Problem("revision_conflict",409)
        row.display_name="Anonymous"
        row.normalized_email=f"{row.pk.hex}@anonymized.invalid"
        row.status="disabled"
        row.verified_at=None
        row.password_hash=make_password(None)
        row.auth_version+=1
        row.revision+=1
        row.save()
        ExternalSession.objects.filter(identity=row,revoked_at=None).update(revoked_at=timezone.now())
        row.identitychallenge_set.filter(consumed_at=None).update(consumed_at=timezone.now())
        result=serialize(row)
    event(row,"privacy_"+operation,"",request.user)
    response=JsonResponse(result)
    response["Cache-Control"]="no-store"
    return response
