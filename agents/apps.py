from django.apps import AppConfig


class AgentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "agents"
    verbose_name = "عامل‌های هوش مصنوعی"

    def ready(self):
        from . import signals  # noqa: F401  (اتصال به رویدادهای پرونده)
