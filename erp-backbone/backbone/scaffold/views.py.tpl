from django.db import transaction
from backbone.access import scopes
from backbone.http import body,endpoint,once,string
from backbone.validation import Problem
from backbone.module_events import publish
from .models import Record

MODULE="__SLUG__"
SCOPE="__SCOPE__"


@endpoint("GET","POST",module=MODULE)
def records(request):
    permission=MODULE+(".view" if request.method=="GET" else ".create")
    allowed=scopes(request.user,permission)
    if not allowed or (SCOPE=="global" and None not in allowed):raise Problem("forbidden",403)
    rows=Record.objects.all()
    if SCOPE!="global":
        path="site_id" if SCOPE=="site" else "space__internal_site_id"
        if None not in allowed:rows=rows.filter(**{path+"__in":allowed})
    fields=["id","name"]+([] if SCOPE=="global" else [SCOPE+"_id"])
    if request.method=="GET":
        if SCOPE=="space":
            from publication.models import PublicationSpace
            try: selected=int(request.GET.get("space_id",""))
            except (ValueError,TypeError):raise Problem("validation_failed",field="space_id")
            spaces=PublicationSpace.objects.filter(pk=selected,active=True,internal_site__active=True)
            if None not in allowed:spaces=spaces.filter(internal_site_id__in=allowed)
            if not spaces.exists():raise Problem("forbidden",403)
            rows=rows.filter(space_id=selected)
        from backbone.business_scope import page
        return page(request.GET,rows.order_by('pk').values(*fields),['name'])
    data=body(request)
    def create():
        values={"name":string(data,"name",160)}
        if SCOPE!="global":
            field=SCOPE+"_id"
            value=data.get(field)
            if type(value) is not int:raise Problem("validation_failed",field=field)
            if SCOPE=="site":
                from backbone.models import Site
                target=Site.objects.select_for_update().filter(pk=value,active=True).first()
                site_id=value
            else:
                from publication.models import PublicationSpace
                target=PublicationSpace.objects.select_for_update().filter(pk=value,active=True,internal_site__active=True).first()
                site_id=target.internal_site_id if target else None
            if not target or (None not in allowed and site_id not in allowed):raise Problem("forbidden",403)
            values[field]=value
        record=Record.objects.create(**values)
        from backbone.access import audit
        audit(request.user,MODULE+".create",record.pk)
        publish(MODULE,"created",record.pk,f"{MODULE}:created:{record.pk}")
        return {key:getattr(record,key) for key in fields}
    with transaction.atomic():
        return once(request,MODULE+".create",data,create)
