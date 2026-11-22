from django.urls import path

from . import views as v

urlpatterns = [
    path("api/marketing/contacts", v.contacts),
    path("api/marketing/contacts/<int:pk>/endpoints", v.add_endpoint),
    path("api/marketing/contacts/<int:pk>/consents", v.consent),
    path("api/marketing/contacts/<int:pk>/forget", v.forget),
    path("api/marketing/segments", v.segments),
    path("api/marketing/templates", v.templates),
    path("api/marketing/templates/<int:pk>/versions", v.versions),
    path("api/marketing/campaigns", v.campaigns),
    path("api/marketing/campaigns/<int:pk>", v.campaign),
    path("api/marketing/campaigns/<int:pk>/preview", v.preview),
    path("api/marketing/campaigns/<int:pk>/metrics", v.metrics),
    path("api/marketing/campaigns/<int:pk>/<slug:action>", v.campaign_action),
    path("api/marketing/suppressions", v.suppressions),
    path("api/marketing/settings/<int:pk>", v.settings_view),
    path("api/marketing/dead-letters", v.dead_letters),
    path("api/marketing/dead-letters/<int:pk>/requeue", v.dead_letters),
    path("api/marketing/surveys", v.surveys),
    path("api/marketing/surveys/<int:pk>/results", v.survey_results),
    path("api/marketing/deliveries/<int:pk>", v.delivery_status),
    path("api/marketing/automation-rules", v.automations),
    path("api/marketing/unsubscribe/<str:token>", v.subscription, {"action":"unsubscribe"}),
    path("api/marketing/confirm/<str:token>", v.subscription, {"action":"confirm"}),
    path("api/marketing/survey-invitations/<str:token>", v.survey_public),
    path("api/marketing/webhooks/<slug:adapter>/<int:pk>", v.webhook),
]
