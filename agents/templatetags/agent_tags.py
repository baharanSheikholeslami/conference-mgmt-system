"""
برچسب‌های قالب عامل‌ها.  استفاده: {% load agent_tags %}

قالب‌های پورتال (اپ workflow) با همین برچسب‌ها خروجی عامل‌ها را نشان می‌دهند؛ اگر عامل‌ها
خاموش باشند (AGENTS_ENABLED=False) هر سه برچسب هیچ چیزی رسم نمی‌کنند و پورتال مثل فاز ۲ می‌ماند.
در حالت سایه (AGENTS_SHADOW_MODE=True) فقط آموزش دانشکده و مدیر سامانه خروجی عامل‌ها را می‌بینند.
"""
from django import template

from agents import services
from agents.models import DocumentValidation

register = template.Library()


@register.inclusion_tag("agents/_validation.html", takes_context=True)
def validation_box(context, version, case=None):
    """
    نتیجه‌ی بررسی خودکار قالب، زیر آخرین نسخه‌ی پیشنهاد یا پایان‌نامه در صفحه‌ی پرونده؛ فقط برای
    نویسنده و فقط تا پایان مرحله‌ی بارگذاری (services.validation_visible_to).
    """
    case = case or version.case
    if not services.validation_visible_to(context["request"].user, case, version):
        return {"show": False}
    validation = DocumentValidation.objects.filter(version=version).first()
    return {"show": True, "version": version, "validation": validation,
            "can_retry": services.can_retry_validation(version, context["request"].user),
            "csrf_token": context.get("csrf_token")}


@register.inclusion_tag("agents/_notifications.html", takes_context=True)
def notifications_card(context, case):
    if not services.visible_to(context["request"].user):
        return {"show": False}
    return {"show": True,
            "notifications": services.visible_notifications(case, context["request"].user)[:20]}


@register.inclusion_tag("agents/_assistant.html", takes_context=True)
def assistant_widget(context):
    user = getattr(context.get("request"), "user", None)
    return {"show": user is not None and user.is_authenticated and services.visible_to(user),
            "csrf_token": context.get("csrf_token")}
