from django.contrib import admin

from .models import (AssistantMessage, DocumentValidation, Notification, PolicyChunk,
                     RefereeSuggestion, SuggestionRun, TopicExtraction)


@admin.register(DocumentValidation)
class DocumentValidationAdmin(admin.ModelAdmin):
    list_display = ("version", "status", "model", "requested_at", "finished_at")
    list_filter = ("status",)


@admin.register(TopicExtraction)
class TopicExtractionAdmin(admin.ModelAdmin):
    list_display = ("version", "status", "title_fa", "verified", "applied", "model", "requested_at")
    list_filter = ("status", "verified", "applied")


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
    list_display = ("case", "purpose", "status", "model", "requested_at", "finished_at")
    list_filter = ("purpose", "status")
    inlines = [RefereeSuggestionInline]


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("subject", "recipient", "kind", "stage", "status", "generated_by", "created_at")
    list_filter = ("kind", "status", "generated_by")
    search_fields = ("subject", "body", "recipient__username")
