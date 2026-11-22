from django.urls import path
from . import views

base="api/bank_reconciliation/"
urlpatterns=[
    path(base+"profiles",views.profiles),path(base+"statements",views.statements),
    path(base+"statements/<int:pk>",views.detail),
    path(base+"statements/<int:pk>/attachments/<int:upload>",views.attachment),
    path(base+"statements/<int:pk>/candidates",views.candidates),
    path(base+"statements/<int:pk>/preview",views.preview),
    path(base+"statements/<int:pk>/<str:action>",views.transition),
]
