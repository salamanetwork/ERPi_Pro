from django.urls import path
from . import views

urlpatterns = [
    path("api/manufacturing/catalog",views.catalog),
    path("api/manufacturing/boms",views.bom_list),
    path("api/manufacturing/boms/<int:pk>",views.bom),
    path("api/manufacturing/orders",views.orders),
    path("api/manufacturing/orders/<int:pk>",views.work),
    path("api/manufacturing/orders/<int:pk>/<slug:operation>",views.work),
    path("api/manufacturing/policies",views.policies),
    path("api/manufacturing/plans",views.plans),
    path("api/manufacturing/plans/<int:pk>",views.plan),
    path("api/manufacturing/proposals/<int:pk>/convert",views.convert),
]
