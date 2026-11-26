from django.urls import path
from . import views

urlpatterns = [
    path('admin/catalog', views.admin_catalog),
    path('terminals/<int:pk>', views.terminal_update),
    path("api/pos/terminals", views.terminals),
    path("api/pos/terminals/<int:pk>", views.terminal_update),
    path("api/pos/catalog", views.catalog),
    path("api/pos/shifts", views.shifts),
    path("api/pos/shifts/<int:pk>/close", views.shift_close),
    path("api/pos/shifts/<int:pk>/report", views.shift_report),
    path("api/pos/sales", views.sales),
    path("api/pos/sales/<int:pk>", views.sale_detail),
    path("api/pos/sales/<int:pk>/void", views.sale_void_refund),
    path("pos/print/receipt/<int:pk>", views.receipt_print),
]
