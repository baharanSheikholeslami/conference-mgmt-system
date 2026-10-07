from django.contrib import admin

from .models import (AssistantMessage, DocumentValidation, Notification, PolicyChunk,
                     RefereeSuggestion, SuggestionRun)


@admin.register(DocumentValidation)
class DocumentValidationAdmin(admin.ModelAdmin):
    list_display = ("version", "status", "model", "requested_at", "finished_at")
    list_filter = ("status",)


@admin.register(PolicyChunk)
class PolicyChunkAdmin(admin.ModelAdmin):
    list_display = ("doc_code", "position", "ref", "embedding_model")
    list_filter = ("doc_code", "embedding_model")
    search_fields = ("ref", "text")
    exclude = ("embedding",)          # بردار هزاربعدی در فرم ادمین خوانا نیست


@admin.register(AssistantMessage)
class AssistantMessageAdmin(admin.ModelAdmin):
    list_display = ("question", "user", "ok", "latency_ms", "created_at")
    list_filter = ("ok",)
    search_fields = ("question", "answer")


class RefereeSuggestionInline(admin.TabularInline):
    model = RefereeSuggestion
    extra = 0


@admin.register(SuggestionRun)
class SuggestionRunAdmin(admin.ModelAdmin):
    list_display = ("case", "status", "model", "requested_at", "finished_at")
    list_filter = ("status",)
    inlines = [RefereeSuggestionInline]


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("subject", "recipient", "kind", "stage", "status", "generated_by", "created_at")
    list_filter = ("kind", "status", "generated_by")
    search_fields = ("subject", "body", "recipient__username")
