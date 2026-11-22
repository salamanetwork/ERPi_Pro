from django.urls import path
from . import views
urlpatterns=[path("api/platform/v1/spaces",views.spaces),path("api/platform/v1/spaces/<int:pk>",views.detail)]

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from backbone.registry import enabled_modules
from . import delivery,editorial


@csrf_exempt
def comments(request,**kwargs):
    if request.method=="POST":
        if "public_identity" not in enabled_modules():return JsonResponse({"error":"login_required"},status=401)
        from .comments import Submit
        return Submit.as_view()(request,**kwargs)
    return delivery.public_comments(request,**kwargs)


base="api/publication/spaces/<int:space_id>/"
urlpatterns += [
    path(base+"entries",editorial.entries),path(base+"entries/<uuid:pk>",editorial.entries),
    path(base+"entries/<uuid:pk>/<slug:action>",editorial.transition),path(base+"menus/<slug:locale>/<slug:location>",editorial.menus),
    path(base+"submissions",editorial.moderate),path(base+"submissions/<uuid:pk>/<slug:action>",editorial.moderate),
    path("robots.txt",delivery.robots),path("sitemap.xml",delivery.sitemap),
    path("api/public/v1/<slug:code>/entries/<str:slug>/comments",comments),
    path("api/public/v1/<slug:code>/menus/<slug:location>",delivery.public_menu)]
for prefix,api in (("api/public/v1/<slug:code>",True),("public/site/<slug:code>",False)):
    urlpatterns += [path(prefix,delivery.deliver,{"api":api}),path(prefix+"/sitemap.xml",delivery.sitemap)]
    for section in ("pages","posts"):
        urlpatterns += [path(prefix+"/"+section,delivery.deliver,{"section":section,"api":api}),
                        path(prefix+"/"+section+"/<str:slug>",delivery.deliver,{"section":section,"api":api})]

# Short public routes and optional trailing slashes share the guarded delivery views.
from django.urls import re_path
short=r"^p/(?P<code>[-\w]+)"
urlpatterns += [re_path(short+r"/?$",delivery.deliver),re_path(short+r"/sitemap\.xml/?$",delivery.sitemap),
    re_path(short+r"/(?P<section>pages|posts)/?$",delivery.deliver),
    re_path(short+r"/(?P<section>pages|posts)/(?P<slug>[^/]+)/?$",delivery.deliver)]
