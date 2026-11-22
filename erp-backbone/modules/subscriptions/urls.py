from django.urls import path
from . import portal,views
from .gl_settings import settings as gl_settings
urlpatterns=[
    path('api/subscriptions/gl-settings', gl_settings),
    path('api/subscriptions/<str:kind>',views.collection),
    path('api/subscriptions/contracts/<int:pk>/billing-preview',views.preview),
    path('api/subscriptions/contracts/<int:pk>/<str:action>',views.action),
    path('api/customer/v1/subscriptions',portal.MyContracts.as_view()),
    path('api/customer/v1/subscriptions/<int:pk>',portal.MyContracts.as_view(http_method_names=['get','head','options'])),
    path('api/customer/v1/subscriptions/<int:pk>/cancellation-requests',portal.MyContracts.as_view(http_method_names=['post','options'])),
    path('api/customer/v1/business/<str:kind>/<int:pk>/download',portal.Download.as_view()),
]
