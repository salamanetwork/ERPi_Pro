from django.urls import path
from .views import records
urlpatterns=[path('api/parties/records',records)]

from backbone.business_api import choices
urlpatterns.append(path('api/parties/choices/<str:kind>',choices('parties')))
from .detail import detail
urlpatterns.append(path('api/parties/records/<int:pk>',detail))
