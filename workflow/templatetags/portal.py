"""فیلترهای قالب: تاریخ شمسی و ارقام فارسی.  استفاده: {% load portal %} ... {{ case.defense_date|jalali }}"""
import os

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static
from django.utils import timezone

from workflow import jalali as j
from workflow import rules
from workflow import services

register = template.Library()


@register.simple_tag
def static_v(path):
    """مثل {% static %} ولی با ?v=زمان‌تغییر فایل، تا مرورگر (به‌ویژه کروم) پس از هر تغییر CSS نسخه‌ی کهنه را از حافظه‌ی نهان نخواند."""
    url = static(path)
    found = finders.find(path)
    return f"{url}?v={int(os.path.getmtime(found))}" if found else url


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


def _countdown(due):
    return rules.countdown(due, timezone.localdate())


@register.filter
def time_left(due):
    """تایمر مهلت به ماه و روز: «۲ ماه و ۱۰ روز مانده»، «امروز آخرین مهلت است»، «۳ روز از مهلت گذشته»."""
    months, days, overdue = _countdown(due)
    if not months and not days:
        return "امروز آخرین مهلت است"
    parts = [f"{j.fa_digits(months)} ماه"] if months else []
    if days:
        parts.append(f"{j.fa_digits(days)} روز")
    return " و ".join(parts) + (" از مهلت گذشته" if overdue else " مانده")


@register.filter
def time_left_class(due):
    """رنگ تایمر: ≤ ۳ روز یا گذشته قرمز، ≤ ۱۴ روز نارنجی، وگرنه آبی."""
    months, days, overdue = _countdown(due)
    total = months * 30 + days
    if overdue or total <= 3:
        return "danger"
    return "warn" if total <= 14 else "calm"
