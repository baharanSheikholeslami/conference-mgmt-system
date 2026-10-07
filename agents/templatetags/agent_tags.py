"""
برچسب‌های قالب عامل‌ها.  استفاده: {% load agent_tags %}

قالب‌های پورتال (اپ workflow) با همین برچسب‌ها خروجی عامل‌ها را نشان می‌دهند؛ اگر عامل‌ها
خاموش باشند (AGENTS_ENABLED=False) هر سه برچسب هیچ چیزی رسم نمی‌کنند و پورتال مثل فاز ۲ می‌ماند.
"""
from django import template

from agents import services
from agents.models import DocumentValidation

register = template.Library()


@register.inclusion_tag("agents/_validation.html", takes_context=True)
def validation_box(context, version):
    """نتیجه‌ی بررسی خودکار قالب، زیر هر نسخه‌ی پیشنهاد در صفحه‌ی پرونده."""
    if not services.enabled():
        return {"show": False}
    validation = DocumentValidation.objects.filter(version=version).first()
    return {"show": True, "version": version, "validation": validation,
            "can_retry": services.can_retry_validation(version, context["request"].user),
            "csrf_token": context.get("csrf_token")}


@register.inclusion_tag("agents/_notifications.html", takes_context=True)
def notifications_card(context, case):
    if not services.enabled():
        return {"show": False}
    return {"show": True,
            "notifications": services.visible_notifications(case, context["request"].user)[:20]}


@register.inclusion_tag("agents/_assistant.html", takes_context=True)
def assistant_widget(context):
    user = getattr(context.get("request"), "user", None)
    return {"show": services.enabled() and user is not None and user.is_authenticated,
            "csrf_token": context.get("csrf_token")}
