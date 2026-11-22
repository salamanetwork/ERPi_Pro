from django.urls import path
from . import views

urlpatterns = [
    path("api/projects",views.index),
    path("api/projects/<int:pk>",views.detail),
    path("api/projects/<int:pk>/tasks",views.tasks),
    path("api/projects/<int:pk>/budget",views.budget),
    path("api/projects/<int:pk>/<slug:operation>",views.mutate),
    path("api/projects/timesheets/<int:pk>",views.sheet_time),
]
