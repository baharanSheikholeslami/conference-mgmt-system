from django.urls import path

from . import api, views

urlpatterns = [
    # کاربران پورتال
    path("assistant/ask/", views.assistant_ask, name="assistant_ask"),
    path("agents/versions/<int:pk>/validate/", views.validation_retry, name="validation_retry"),
    path("agents/cases/<int:pk>/suggestions/", views.suggestions_refresh, name="suggestions_refresh"),

    # n8n (با توکن)
    path("api/agents/validations/<int:pk>/result/", api.validation_result, name="api_validation_result"),
    path("api/agents/referee-suggestions/<int:pk>/result/", api.referee_suggestions_result,
         name="api_referee_suggestions_result"),
    path("api/agents/rag/search/", api.rag_search, name="api_rag_search"),
    path("api/agents/reminders/due/", api.reminders_due, name="api_reminders_due"),
    path("api/agents/notifications/", api.notification_record, name="api_notification_record"),
]
