from django.urls import path
from . import views, admin_views
urlpatterns=[path("public/account",views.account_page),path("api/platform/v1/identities",admin_views.identities),path("api/platform/v1/identities/<uuid:pk>",admin_views.identities),path("api/public/v1/auth/csrf",views.CSRF.as_view()),
 path("api/public/v1/auth/password-reset/request",views.Auth.as_view(),{"action":"password-reset-request"}),
 path("api/public/v1/auth/password-reset/confirm",views.Auth.as_view(),{"action":"password-reset-confirm"}),
 path("api/customer/v1/me",views.Me.as_view()),path("api/public/v1/auth/logout",views.Logout.as_view()),
 path("api/public/v1/auth/logout-all",views.Logout.as_view(),{"all_sessions":True}),
 path("public/auth/confirm",views.confirm_page),path("api/public/v1/auth/<slug:action>",views.Auth.as_view())]
from .business import Records,Slots,PrintDocument
urlpatterns += [path('api/customer/v1/business/<str:kind>',Records.as_view()),
    path('api/customer/v1/business/resources/<int:pk>/slots',Slots.as_view()),
    path('api/customer/v1/business/<str:kind>/<int:pk>/print',PrintDocument.as_view())]
