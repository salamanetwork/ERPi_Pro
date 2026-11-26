import hashlib
from django.db import transaction
from django.http import FileResponse,HttpResponse
from django.core.files.storage import storages
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.exceptions import NotFound,ValidationError
from backbone.http import endpoint,require,once,body
from backbone.registry import enabled_modules
from backbone.access import audit
from backbone.validation import Problem
from publication.models import PublicationSpace
from publication.views import scoped
from public_identity.views import CustomerView
from public_identity.services import event
from .models import MediaAsset,UploadReceipt
from .services import metadata,validate,create

def active_rows():
    return MediaAsset.objects.filter(deleted_at=None,space__active=True,space__internal_site__active=True)

def stream(row):
    try: handle=storages[row.storage_alias].open(row.object_key,"rb")
    except FileNotFoundError: raise NotFound()
    response=FileResponse(handle,as_attachment=True,filename=row.original_name,content_type=row.mime)
    response["X-Content-Type-Options"]="nosniff";response["Cache-Control"]="private, no-store"
    response["Content-Security-Policy"]="default-src 'none'; sandbox"
    return response

class CustomerMedia(CustomerView):
    def initial(self,request,*args,**kwargs):
        super().initial(request,*args,**kwargs)
        if "media_service" not in enabled_modules(): raise NotFound()
    def get(self,request,pk=None,download=False):
        rows=active_rows().filter(external_owner_id=request.user.pk,space_id=request.auth.space_id)
        if pk:
            row=rows.filter(pk=pk).first()
            if not row: raise NotFound()
            return stream(row) if download else Response(metadata(row))
        from .folders import listing
        return listing(request,rows)
    def post(self,request):
        name,mime,content=validate(request.FILES.get("file"))
        key=request.headers.get("Idempotency-Key","")
        if not key.strip() or len(key)>120: raise ValidationError({"idempotency_key":["Supply Idempotency-Key up to 120 characters."]})
        fingerprint=hashlib.sha256(content+name.encode()).hexdigest()
        with transaction.atomic():
            receipt,fresh=UploadReceipt.objects.select_for_update().get_or_create(identity=request.user.identity,key=key,defaults={"fingerprint":fingerprint})
            if not fresh:
                if receipt.fingerprint!=fingerprint or not receipt.asset_id or receipt.asset.space_id!=request.auth.space_id:
                    return Response({"error":"idempotency_conflict"},status=409)
                return Response(metadata(receipt.asset))
            row=create(request.auth.space,name,mime,content,identity=request.user.identity)
            receipt.asset=row;receipt.save(update_fields=["asset"])
            event(request.user.identity,"media_uploaded",row.pk)
        return Response(metadata(row),status=201)
    def delete(self,request,pk):
        with transaction.atomic():
            row=active_rows().select_for_update(of=("self",)).filter(pk=pk,external_owner_id=request.user.pk,space_id=request.auth.space_id).first()
            if not row: raise NotFound()
            if request.data.get("revision")!=row.revision: return Response({"error":"revision_conflict"},status=409)
            row.deleted_at=timezone.now();row.revision+=1;row.save(update_fields=["deleted_at","revision"])
            event(request.user.identity,"media_archived",row.pk)
        return Response({"status":"archived"})

@endpoint("GET","POST",module="media_service")
def staff_media(request,pk=None,download=False):
    permission="media_service.view" if request.method=="GET" else "media_service.manage"
    require(request.user,permission)
    if "publication" not in enabled_modules(): raise Problem("module_disabled",404)
    allowed=scoped(request.user,"media_service.view" if request.method=="GET" else "media_service.manage")
    if request.method=="GET":
        rows=active_rows().filter(space__in=allowed)
        if pk:
            row=rows.filter(pk=pk).first()
            if not row: raise Problem("not_found",404)
            try: return stream(row) if download else metadata(row)
            except NotFound: raise Problem("not_found",404)
        try: site=int(request.GET.get("space_id",0));page=max(1,int(request.GET.get("page",1)))
        except ValueError: raise Problem("invalid_space_id",field="space_id")
        rows=rows.filter(space_id=site).order_by("-created_at");count=rows.count();start=(page-1)*25
        if "page_size" in request.GET:
            from backbone.services.pagination import paginate,search_rows
            return paginate(request,search_rows(rows,request.GET,['original_name', 'mime']),metadata)
        return {"count":count,"next":f"?space_id={site}&page={page+1}" if start+25<count else None,
            "previous":f"?space_id={site}&page={page-1}" if page>1 else None,"results":[metadata(r) for r in rows[start:start+25]]}
    try: site=allowed.get(pk=int(request.POST.get("space_id",0)),active=True)
    except (PublicationSpace.DoesNotExist,ValueError): raise Problem("invalid_space_id",field="space_id")
    try: name,mime,content=validate(request.FILES.get("file"))
    except ValidationError: raise Problem("invalid_file",field="file",reason="invalid_image")
    visibility=request.POST.get("visibility","private")
    if visibility not in {"public","private"}: raise Problem("invalid_visibility",field="visibility")
    fingerprint={"space_id":site.pk,"name":name,"hash":hashlib.sha256(content).hexdigest(),"visibility":visibility}
    def save():
        row=create(site,name,mime,content,staff=request.user,visibility=visibility)
        audit(request.user,"media.upload",row.pk);return metadata(row)
    return once(request,"media.upload",fingerprint,save)

@endpoint("GET",public=True,module="media_service")
def public_download(request,pk):
    if "publication" not in enabled_modules(): raise Problem("module_disabled",404)
    row=active_rows().filter(pk=pk,visibility="public").first()
    if not row: raise Problem("not_found",404)
    from django.utils.cache import get_conditional_response
    tag=f'"{row.sha256}-{row.revision}"'
    response=get_conditional_response(request,etag=tag)
    if response is None:
        try: response=stream(row)
        except NotFound: raise Problem("not_found",404)
        if row.mime.startswith("image/"):response["Content-Disposition"]="inline"
    response["ETag"]=tag
    response["Cache-Control"]="no-cache, must-revalidate"
    return response


@endpoint("POST",module="media_service")
def promote(request,pk):
    from backbone.http import body
    from backbone.services.outbox import enqueue
    data=body(request)
    with transaction.atomic():
        row=active_rows().select_for_update(of=("self",)).filter(pk=pk).first()
        if not row:raise Problem("not_found",404)
        require(request.user,"media_service.manage",row.space.internal_site_id)
        def apply():
            if type(data.get("revision")) is not int or data["revision"]!=row.revision:raise Problem("revision_conflict",409)
            if row.external_owner_id or not row.staff_owner_id or row.mime not in ("image/png","image/jpeg","image/webp"):
                raise Problem("validation_failed",field="asset_id")
            if row.visibility!="public":
                row.visibility="public"
                row.revision+=1
                row.save(update_fields=["visibility","revision"])
                audit(request.user,"media.promote_public",pk)
                enqueue(key=f"media:{pk}:{row.revision}:public",module="media_service",object_id=row.space_id,event="promote_public",recipients=[])
            return metadata(row)
        return once(request,"media.promote",{"id":str(pk),"data":data},apply)
