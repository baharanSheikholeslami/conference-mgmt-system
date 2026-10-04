"""فیلترهای قالب: تاریخ شمسی و ارقام فارسی.  استفاده: {% load portal %} ... {{ case.defense_date|jalali }}"""
from django import template
from django.utils import timezone

from workflow import jalali as j
from workflow import services

register = template.Library()


@register.filter
def jalali(value):
    return j.to_jalali(value) if value else "—"


@register.filter
def jalali_datetime(value):
    if not value:
        return "—"
    local = timezone.localtime(value)
    return f"{j.to_jalali(local)}، {j.fa_digits(local.strftime('%H:%M'))}"


@register.filter
def fa(value):
    return "" if value is None else j.fa_digits(value)


@register.filter
def action_label(action):
    return services.action_label(action)
