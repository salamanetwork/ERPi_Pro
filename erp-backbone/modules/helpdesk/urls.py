from django.urls import path
from . import views

urlpatterns = [
    path("api/workspace/notifications/status",views.delivery_health),
    path("api/helpdesk/tickets",views.tickets),
    path("api/helpdesk/tickets/<int:pk>",views.ticket),
    path("api/helpdesk/tickets/<int:pk>/comments",views.comments),
    path("api/helpdesk/tickets/<int:pk>/events",views.events),
    path("api/helpdesk/tickets/<int:pk>/<slug:operation>",views.action),
    path("api/helpdesk/categories",views.configuration,{"kind":"categories"}),
    path("api/helpdesk/categories/<int:pk>",views.category_detail),
    path("api/helpdesk/sla-policies",views.configuration,{"kind":"sla-policies"}),
]
