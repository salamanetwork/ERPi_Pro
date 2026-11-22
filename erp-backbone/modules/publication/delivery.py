import hashlib
import json
from urllib.parse import quote
from xml.etree.ElementTree import Element,SubElement,tostring
import redis
from django.conf import settings
from django.http import Http404,HttpResponse,HttpResponsePermanentRedirect,JsonResponse
from django.shortcuts import render
from django.utils.cache import get_conditional_response
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_safe
from backbone.registry import enabled_modules
from .models import PublicationSpace,ContentEntry,NavigationMenu,PublicSubmission,SlugRedirect
from .editorial import menu_tree


def live_spaces():
    if "publication" not in enabled_modules():raise Http404()
    return PublicationSpace.objects.filter(active=True,internal_site__active=True)


def site(code):
    row=live_spaces().filter(code=code).first()
    if not row:raise Http404()
    return row


def language(request,space):
    lang=request.GET.get("lang",space.default_language)
    if lang not in ("en","ar"):raise Http404()
    return lang


def path_for(space,row,api=False):
    prefix="/api/public/v1/" if api else "/public/site/"
    return f"{prefix}{space.code}/{row.entry_type}s/{quote(row.published_slug,safe='')}?lang={row.locale}"


def cache():
    return redis.Redis.from_url(settings.CELERY_BROKER_URL,socket_connect_timeout=.15,socket_timeout=.15)


def guarded(response):
    response["Cache-Control"]="no-cache, must-revalidate"
    return response


def cached(request,space,key,ctype,build):
    version=json.dumps([request.build_absolute_uri(request.path),space.pk,space.cache_epoch,space.revision,key])
    digest=hashlib.sha256(version.encode()).hexdigest()
    tag=f'"{digest}"'
    response=get_conditional_response(request,etag=tag)
    if response is None:
        client=cache()
        key="erpi:publication:"+digest
        try:data=client.get(key)
        except (redis.RedisError,OSError):data=None
        if data is None:
            response=build()
            try:client.setex(key,300,response.content)
            except (redis.RedisError,OSError):pass
        else:response=HttpResponse(data,content_type=ctype)
    response["ETag"]=tag
    return guarded(response)


def listing(space,lang):
    return ContentEntry.objects.filter(space=space,status="published",locale=lang).order_by("-published_at","pk")


def approved(row):
    return list(PublicSubmission.objects.filter(entry=row,status="approved").order_by("created_at","pk").values("author_display_name","body_text","created_at"))


def nav(space,lang,loc):
    return menu_tree(NavigationMenu.objects.filter(space=space,locale=lang,location=loc).first(),True)


@require_safe
def deliver(request,code,section="",slug=None,api=False):
    space=site(code)
    lang=language(request,space)
    row=None
    if slug:
        namespace=ContentEntry.objects.filter(space=space,locale=lang,entry_type=section[:-1])
        row=namespace.filter(published_slug=slug).first()
        redirected=False
        if row is None:
            alias=SlugRedirect.objects.filter(space=space,locale=lang,entry_type=section[:-1],old_slug=slug).first()
            if alias:
                row=namespace.filter(pk=alias.target_entry_id,published_slug=alias.new_slug).first()
                redirected=True
        if not row or row.status not in ("published","archived"):return guarded(HttpResponse(status=404))
        if row.status=="archived":return guarded(HttpResponse(status=410))
        if redirected:return guarded(HttpResponsePermanentRedirect(path_for(space,row,api)))
    def build():
        cards=[]
        home="/public/site/"+space.code
        data=row.published_data if row else {}
        if not row:
            rows=listing(space,lang)
            if section:rows=rows.filter(entry_type=section[:-1])
            cards=[{"title":e.published_data["title"],"summary":e.published_data["summary"],"slug":e.published_slug,"url":path_for(space,e),"entry_type":e.entry_type} for e in rows]
        if api:return JsonResponse({**data,"slug":row.published_slug,"locale":lang,"entry_type":row.entry_type} if row else {"results":cards})
        seo=data.get("seo_meta",{})
        title=seo.get("title") or data.get("title") or space.name
        desc=seo.get("description") or data.get("summary") or space.name
        canonical=request.build_absolute_uri(path_for(space,row) if row else home+("/"+section if section else "")+"?lang="+lang)
        siblings=ContentEntry.objects.filter(space=space,entry_type=row.entry_type,translation_group_id=row.translation_group_id,status="published") if row else []
        cover=seo.get("og_image_media") or data.get("cover_media_id")
        crumbs=[{"@type":"ListItem","position":1,"name":space.name,"item":request.build_absolute_uri(home)}]
        if row:crumbs.append({"@type":"ListItem","position":2,"name":title,"item":canonical})
        ld={"@context":"https://schema.org","@graph":[{"@type":"Article" if section=="posts" and row else "WebPage","headline":title,"url":canonical,"inLanguage":lang,"description":desc},{"@type":"BreadcrumbList","itemListElement":crumbs}]}
        safe=json.dumps(ld).translate({60:r"\u003c",62:r"\u003e",38:r"\u0026"})
        other="ar" if lang=="en" else "en"
        alternate=listing(space,other)
        if section: alternate=alternate.filter(entry_type=section[:-1])
        sibling=alternate.filter(translation_group_id=row.translation_group_id).first() if row else None
        alternate_url=path_for(space,sibling) if sibling else home+("/"+section if section else "")+"?lang="+other
        return render(request,"publication/site.html",{"title":title,"description":desc,"site":space,"row":row,"data":data,"cards":cards,"section":section,"pages":[c for c in cards if c["entry_type"]=="page"],
            "posts":[c for c in cards if c["entry_type"]=="post"],"other":other,"other_url":alternate_url,"other_count":alternate.count(),
            "home":home,"lang":lang,"canonical":canonical,"robots":seo.get("robots","index,follow"),"ld":mark_safe(safe),
            "og":request.build_absolute_uri("/api/public/v1/media/"+cover+"/download") if cover else "",
            "translations":[(e.locale,request.build_absolute_uri(path_for(space,e))) for e in siblings],
            "header":nav(space,lang,"header"),"footer":nav(space,lang,"footer"),"comments":approved(row) if row else [],
            "comment_url":f"/api/public/v1/{code}/entries/{quote(row.published_slug)}/comments?lang={lang}&type={row.entry_type}" if row else ""})
    return cached(request,space,["2.6.2",section,lang,api],"application/json" if api else "text/html; charset=utf-8",build)


@require_safe
def public_menu(request,code,location):
    space=site(code)
    lang=language(request,space)
    if location not in ("header","footer"):raise Http404()
    return cached(request,space,[location,lang],"application/json",lambda:JsonResponse({"items":nav(space,lang,location)}))


def comment_entry(request,space,slug,lock=False):
    rows=listing(space,language(request,space)).filter(published_slug=slug)
    if request.GET.get("type"):rows=rows.filter(entry_type=request.GET["type"])
    if lock:rows=rows.select_for_update()
    result=list(rows[:2])
    if len(result)!=1:raise Http404()
    return result[0]


@require_safe
def public_comments(request,code,slug):
    space=site(code)
    row=comment_entry(request,space,slug)
    return cached(request,space,["comments",str(row.pk)],"application/json",lambda:JsonResponse({"results":approved(row)}))


def conditional(request,data,mime):
    tag='"'+hashlib.sha256(data).hexdigest()+'"'
    response=get_conditional_response(request,etag=tag)
    if response is None:response=HttpResponse(data,content_type=mime)
    response["ETag"]=tag
    return guarded(response)


@require_safe
def sitemap(request,code=None):
    ns="http://www.sitemaps.org/schemas/sitemap/0.9"
    if code:
        space=site(code)
        def build():
            root=Element("urlset",xmlns=ns)
            from .tasks import sitemap_key,sitemap_paths
            try:warm=cache().get(sitemap_key(space))
            except (redis.RedisError,OSError):warm=None
            urls=json.loads(warm) if warm else sitemap_paths(space)
            for url in urls:SubElement(SubElement(root,"url"),"loc").text=request.build_absolute_uri(url)
            return HttpResponse(tostring(root,encoding="utf-8",xml_declaration=True),content_type="application/xml")
        return cached(request,space,"sitemap","application/xml",build)
    root=Element("sitemapindex",xmlns=ns)
    for s in live_spaces().order_by("pk"):SubElement(SubElement(root,"sitemap"),"loc").text=request.build_absolute_uri("/public/site/"+s.code+"/sitemap.xml")
    return conditional(request,tostring(root,encoding="utf-8",xml_declaration=True),"application/xml")


@require_safe
def robots(request):
    return conditional(request,("User-agent: *\nAllow: /public/site/\nDisallow: /admin/\nDisallow: /public/account\nSitemap: "+request.build_absolute_uri("/sitemap.xml")+"\n").encode(),"text/plain")
