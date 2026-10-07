"""
نقطه‌ی اتصال گردش‌کار به عامل‌ها.

موتور گردش‌کار (workflow/services.py) از وجود عامل‌ها خبر ندارد؛ فقط برای هر اقدام یک CaseEvent
ثبت می‌کند. این‌جا به ثبت همان رویدادها گوش می‌دهیم و «پس از قطعی‌شدن تراکنش» (on_commit)
عامل‌ها را خبر می‌کنیم. پس اگر اقدامی به هر دلیل لغو (rollback) شود، هیچ عاملی هم خبردار نمی‌شود.
"""
from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from workflow.models import CaseEvent


@receiver(post_save, sender=CaseEvent, dispatch_uid="agents.on_case_event")
def on_case_event(sender, instance, created, **kwargs):
    if not created or not settings.AGENTS_ENABLED:
        return
    from . import services                       # دیرهنگام، تا بارگذاری اپ‌ها حلقه‌ای نشود
    transaction.on_commit(lambda: services.handle_event(instance.pk))
