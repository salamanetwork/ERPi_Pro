from backbone.http import endpoint, require, body, string, identifier, once
from backbone.access import visible, audit, has_permission
from backbone.models import Site
from backbone.validation import Problem
from backbone.site_admin import require_active_site
from django.db import transaction, IntegrityError
from django.core.validators import validate_slug
from django.core.exceptions import ValidationError
from .models import PublicationSpace

def data(row):
    return {"id":row.pk,"code":row.code,"name":row.name,"default_language":row.default_language,
            "site_id":row.internal_site_id,"active":row.active,"revision":row.revision}

def scoped(user,permission):
    # Scope through the existing Site, never through a client-supplied membership.
    from backbone.access import scopes
    allowed=scopes(user,permission)
    rows=PublicationSpace.objects.all()
    return rows if None in allowed else rows.filter(internal_site_id__in=allowed)

@endpoint("GET","POST", module="publication")
def spaces(request):
    require(request.user,"publication.view" if request.method=="GET" else "publication.manage")
    if request.method=="GET":
        rows=scoped(request.user,"publication.view").order_by("id")
        try: page=max(1,int(request.GET.get("page",1)))
        except ValueError: raise Problem("invalid_page",field="page")
        if "page_size" in request.GET:
            from backbone.services.pagination import paginate,search_rows
            return paginate(request,search_rows(rows,request.GET,['name', 'code']),data)
        count=rows.count();start=(page-1)*25
        return {"count":count,"next":f"?page={page+1}" if start+25<count else None,
                "previous":f"?page={page-1}" if page>1 else None,"results":[data(r) for r in rows[start:start+25]]}
    payload=body(request)
    def create():
        site=identifier(payload.get("site_id"),"site_id")
        require(request.user,"publication.manage",site)
        require_active_site(site,lock=True)
        code=string(payload,"code",50).lower()
        try: validate_slug(code)
        except ValidationError: raise Problem("invalid_code",field="code")
        language=payload.get("default_language","en")
        if language not in {"en","ar"}: raise Problem("invalid_default_language",field="default_language")
        try:
            with transaction.atomic():
                row=PublicationSpace.objects.create(code=code,name=string(payload,"name",120),internal_site_id=site,default_language=language)
        except IntegrityError: raise Problem("invalid_code",field="code",reason="duplicate")
        audit(request.user,"publication.create",row.pk)
        return data(row)
    return once(request,"publication.create",payload,create)

@endpoint("GET","PATCH",module="publication")
def detail(request,pk):
    permission="publication.view" if request.method=="GET" else "publication.manage"
    require(request.user,permission)
    if request.method=="GET":
        row=scoped(request.user,permission).filter(pk=pk).first()
        if not row: raise Problem("not_found",404)
        return data(row)
    payload=body(request)
    with transaction.atomic():
        row=scoped(request.user,permission).select_for_update().filter(pk=pk).first()
        if not row: raise Problem("not_found",404)
        if payload.get("revision")!=row.revision: raise Problem("revision_conflict",409)
        if "name" in payload: row.name=string(payload,"name",120)
        if "active" in payload:
            if type(payload["active"]) is not bool: raise Problem("invalid_active",field="active")
            row.active=payload["active"]
        if "default_language" in payload:
            if payload["default_language"] not in {"en","ar"}: raise Problem("invalid_default_language",field="default_language")
            row.default_language=payload["default_language"]
        row.revision+=1;row.save()
        audit(request.user,"publication.update",row.pk)
        return data(row)
