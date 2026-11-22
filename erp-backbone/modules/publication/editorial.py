from functools import wraps
from django.db import IntegrityError,transaction
from django.utils import timezone
from backbone.access import audit
from backbone.http import body,endpoint,once
from backbone.services.outbox import enqueue
from backbone.validation import Problem
from .fields import choice,content,image,invalid,link,slug,text,uid
from .models import ContentEntry,ContentRevision,MenuItem,NavigationMenu,PublicSubmission,SlugRedirect
from .views import scoped


def staff(*methods):
    def decorate(fn):
        @endpoint(*methods,module="publication")
        @wraps(fn)
        def wrapped(request,*args,**kwargs):
            if request.headers.get("Authorization"): raise Problem("forbidden",403)
            try:
                with transaction.atomic(): return fn(request,*args,**kwargs)
            except IntegrityError: raise Problem("publication_conflict",409) from None
        return wrapped
    return decorate


def space_for(request,pk,permission):
    rows=scoped(request.user,"publication."+permission)
    if request.method!="GET": rows=rows.select_for_update()
    space=rows.filter(pk=pk).first()
    if not space: raise Problem("forbidden",403)
    return space


def revision(data,row):
    if type(data.get("revision")) is not int or data["revision"]!=(row.revision if row else 0):
        raise Problem("revision_conflict",409)


def record(space,user,obj,action,public=True):
    if public:
        space.cache_epoch+=1
        space.save(update_fields=["cache_epoch"])
        from .models import SitemapRefresh
        SitemapRefresh.objects.update_or_create(space=space,defaults={"epoch":space.cache_epoch,"due_at":timezone.now()})
    audit(user,"publication."+action,obj.pk)
    enqueue(key=f"publication:{obj.pk}:{obj.revision}:{action}",module="publication",object_id=space.pk,event=action,recipients=[])


def entry_json(row):
    return {**{k:getattr(row,k) for k in ("locale","entry_type","status","working_slug","published_slug","working_data",
        "working_in_review","has_unpublished_changes","revision")},"id":str(row.pk),"translation_group_id":str(row.translation_group_id),
        "published_at":row.published_at.isoformat() if row.published_at else None}


@staff("GET","POST","PATCH")
def entries(request,space_id,pk=None):
    space=space_for(request,space_id,"view" if request.method=="GET" else "manage")
    rows=ContentEntry.objects.filter(space=space)
    row=rows.filter(pk=pk).first() if pk else None
    if pk and not row: raise Problem("not_found",404)
    if request.method=="GET" and not pk and "page_size" in request.GET:
        from backbone.services.pagination import paginate,search_rows
        found=search_rows(rows,request.GET,("working_slug","working_data__title","locale","status"))
        result=paginate(request,found.order_by("-updated_at","pk"),entry_json)
        result["choices"]=[{"id":str(key),"entry_type":kind,"locale":lang,"translation_group_id":str(group),"working_data":{"title":title}}
            for key,kind,lang,group,title in rows.order_by("working_slug","pk").values_list("pk","entry_type","locale","translation_group_id","working_data__title")]
        return result
    if request.method=="GET": return entry_json(row) if row else {"results":[entry_json(r) for r in rows.order_by("-updated_at")]}
    data=body(request)
    if request.method=="POST" and not pk:
        row=ContentEntry(space=space,created_by=request.user,entry_type=choice(data.get("entry_type"),("page","post"),"entry_type"),
            locale=choice(data.get("locale"),("en","ar"),"locale"))
        if data.get("translation_of"):
            other=rows.filter(pk=uid(data["translation_of"],"translation_of"),entry_type=row.entry_type).exclude(locale=row.locale).first()
            if not other: invalid("translation_of")
            row.translation_group_id=other.translation_group_id
    elif request.method=="PATCH" and row:
        revision(data,row)
        if row.status=="archived": raise Problem("invalid_state",409)
        row.revision+=1
        if row.status=="in_review": row.status="draft"
    else: raise Problem("method_not_allowed",405)
    row.working_in_review=False
    row.working_slug=slug(data.get("working_slug",row.working_slug))
    if "working_data" in data or not row.working_data: row.working_data=content(space,data.get("working_data"))
    row.has_unpublished_changes=(row.working_slug!=row.published_slug or row.working_data!=row.published_data)
    row.updated_by=request.user
    row.save()
    audit(request.user,"publication.edit",row.pk)
    return entry_json(row)


@staff("POST")
def transition(request,space_id,pk,action):
    choice(action,("submit-review","publish","unpublish","archive"),"action")
    space=space_for(request,space_id,"manage" if action=="submit-review" else "publish")
    data=body(request)
    def apply():
        row=ContentEntry.objects.filter(space=space,pk=pk).first()
        if not row: raise Problem("not_found",404)
        revision(data,row)
        if row.status=="archived": raise Problem("invalid_state",409)
        row.revision+=1
        if action=="submit-review":
            if row.working_in_review or not row.has_unpublished_changes: raise Problem("invalid_state",409)
            row.working_in_review=True
            if row.status!="published":row.status="in_review"
        elif action=="publish":
            snap=row.working_data
            image(space,snap["cover_media_id"],"cover_media_id")
            image(space,snap["seo_meta"]["og_image_media"],"og_image_media")
            for b in snap["body_blocks"]:
                if b["type"]=="image": image(space,b["media_id"],"media_id")
            aliases=SlugRedirect.objects.filter(space=space,entry_type=row.entry_type,locale=row.locale)
            if aliases.filter(old_slug=row.working_slug).exclude(target_entry=row).exists(): raise Problem("slug_reserved",409)
            # Canonicalise every alias to the new live slug, including a rename back.
            aliases.filter(target_entry=row,old_slug=row.working_slug).delete()
            aliases.filter(target_entry=row).update(new_slug=row.working_slug)
            if row.published_slug and row.published_slug!=row.working_slug:
                aliases.create(space=space,entry_type=row.entry_type,locale=row.locale,old_slug=row.published_slug,new_slug=row.working_slug,target_entry=row)
            row.published_data=snap
            row.published_slug=row.working_slug
            row.published_at=row.published_at or timezone.now()
            row.status="published"
            row.has_unpublished_changes=False
            ContentRevision.objects.create(space=space,entry=row,revision_number=row.revision,slug=row.published_slug,snapshot_data=snap,published_by=request.user)
        elif action=="unpublish":
            if row.status!="published": raise Problem("invalid_state",409)
            row.status="draft"
            row.has_unpublished_changes=True
        else: row.status="archived"
        if action!="submit-review":row.working_in_review=False
        row.updated_by=request.user
        row.save()
        record(space,request.user,row,action,action!="submit-review")
        return entry_json(row)
    return once(request,"publication."+action,{"id":str(pk),"data":data},apply)


def menu_tree(menu,live=False):
    if not menu:return []
    rows=list(menu.items.select_related("target_entry").order_by("sort_order","pk"))
    def children(parent):
        out=[]
        for r in rows:
            if r.parent_id!=parent:continue
            e=r.target_entry
            if live and (not r.is_active or (e and (e.status!="published" or e.space_id!=menu.space_id or e.locale!=menu.locale))):continue
            from .delivery import path_for
            node={"label":r.label,"href":path_for(menu.space,e) if e and e.published_slug else r.target_path or r.external_url,"children":children(r.pk)}
            if not live:node.update(target_type=r.target_type,target_entry=str(r.target_entry_id or ""),target_path=r.target_path,external_url=r.external_url,is_active=r.is_active)
            out.append(node)
        return out
    return children(None)


@staff("GET","PUT")
def menus(request,space_id,locale,location):
    space=space_for(request,space_id,"view" if request.method=="GET" else "manage")
    choice(locale,("en","ar"),"locale")
    choice(location,("header","footer"),"location")
    menu=NavigationMenu.objects.filter(space=space,locale=locale,location=location).first()
    if request.method=="GET": return {"revision":menu.revision if menu else 0,"items":menu_tree(menu)}
    data=body(request)
    revision(data,menu)
    if not menu:menu=NavigationMenu(space=space,locale=locale,location=location)
    menu.revision+=1
    menu.save()
    menu.items.all().delete()
    def add(items,parent=None):
        if not isinstance(items,list) or len(items)>60: invalid("items")
        for i,v in enumerate(items):
            if not isinstance(v,dict):invalid("items")
            kind=choice(v.get("target_type"),("entry","path","external_url"),"target_type")
            opt={}
            if kind=="entry":
                e=ContentEntry.objects.filter(space=space,locale=locale,pk=uid(v.get("target_entry"),"target_entry")).first()
                if not e:invalid("target_entry")
                opt["target_entry"]=e
            else:
                field="target_path" if kind=="path" else "external_url"
                opt[field]=link(v.get(field),field)
            active=v.get("is_active",True)
            if type(active) is not bool:invalid("is_active")
            item=MenuItem.objects.create(menu=menu,parent=parent,label=text(v.get("label"),"label",150,1),target_type=kind,is_active=active,sort_order=i,**opt)
            if v.get("children"):
                if parent:invalid("children")
                add(v["children"],item)
    add(data.get("items"))
    record(space,request.user,menu,"menu_update")
    return {"revision":menu.revision,"items":menu_tree(menu)}


@staff("GET","POST")
def moderate(request,space_id,pk=None,action=None):
    space=space_for(request,space_id,"moderate")
    rows=PublicSubmission.objects.filter(space=space)
    def item(x):return {"id":str(x.pk),"entry_id":str(x.entry_id),"status":x.status,"body_text":x.body_text,"author":x.author_display_name,"reason":x.moderation_reason,"revision":x.revision}
    if request.method=="GET" and not pk and "page_size" in request.GET:
        from backbone.services.pagination import paginate,search_rows
        if state:=request.GET.get("status"):rows=rows.filter(status=state)
        return paginate(request,search_rows(rows,request.GET,("author_display_name","body_text")).order_by("-created_at","pk"),item)
    if request.method=="GET" and not pk:return {"results":[item(x) for x in rows.order_by("-created_at")]}
    states={"approve":"approved","reject":"rejected","hide":"hidden"}
    if request.method!="POST" or action not in states:raise Problem("method_not_allowed",405)
    data=body(request)
    def apply():
        row=rows.filter(pk=pk).first()
        if not row:raise Problem("not_found",404)
        revision(data,row)
        if states[action]==row.status or (action=="hide" and row.status!="approved"):raise Problem("invalid_state",409)
        row.status=states[action]
        row.moderated_at=timezone.now()
        row.moderated_by=request.user
        row.moderation_reason=text(data.get("reason",""),"reason",500)
        row.revision+=1
        row.save()
        record(space,request.user,row,action)
        return item(row)
    return once(request,"publication."+action,{"id":str(pk),"data":data},apply)
