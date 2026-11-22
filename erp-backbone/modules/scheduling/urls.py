from django.urls import path
from . import views
urlpatterns=[path('api/scheduling/resources',views.resources),path('api/scheduling/appointments',views.appointments)]

from backbone.business_api import choices
urlpatterns.append(path('api/scheduling/choices/<str:kind>',choices('scheduling')))
