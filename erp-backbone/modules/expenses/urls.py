from django.urls import path
from . import views
urlpatterns=[
    path("api/expenses/choices",views.choices),
    path("api/expenses/settlements",views.settlements),
    path("api/expenses/claims",views.claims),
    path("api/expenses/claims/<int:pk>",views.claim),
    path("api/expenses/claims/<int:pk>/lines/<int:line>/receipt",views.receipt),
    path("api/expenses/claims/<int:pk>/<str:action>",views.transition),
]
