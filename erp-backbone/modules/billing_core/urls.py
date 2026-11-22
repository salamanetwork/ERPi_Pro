from django.urls import path
from . import views
urlpatterns=[path('api/billing_core/invoices',views.invoices),path('api/billing_core/receipts',views.receipts),
    path('api/billing_core/<str:kind>/<int:pk>/print',views.printable),path('api/billing_core/invoices/<int:pk>/<str:action>',views.action)]

from backbone.business_api import choices
urlpatterns.append(path('api/billing_core/choices/<str:kind>',choices('billing_core')))
