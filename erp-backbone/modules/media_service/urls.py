from django.urls import path
from . import views, folders
urlpatterns=[path("api/customer/v1/media",folders.Files.as_view()),path("api/customer/v1/media/<uuid:pk>",folders.Files.as_view()),
 path("api/customer/v1/media/<uuid:pk>/download",folders.Files.as_view(),{"download":True}),
 path("api/platform/v1/media",views.staff_media),path("api/platform/v1/media/<uuid:pk>",views.staff_media),
 path("api/platform/v1/media/<uuid:pk>/download",views.staff_media,{"download":True}),path("api/public/v1/media/<uuid:pk>/download",views.public_download)]

urlpatterns.append(path("api/media-service/assets/<uuid:pk>/promote-public",views.promote))

urlpatterns += [path('api/customer/v1/media/folders',folders.Folders.as_view()),path('api/customer/v1/media/folders/<uuid:pk>',folders.Folders.as_view()),path('api/customer/v1/media/<uuid:pk>/thumbnail',folders.Thumbnail.as_view())]
