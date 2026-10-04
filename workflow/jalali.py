"""
توابع کمکی تاریخ شمسی و ارقام فارسی.
در پایگاه‌داده همه‌ی تاریخ‌ها میلادی ذخیره می‌شوند؛ تبدیل به شمسی فقط هنگام نمایش و دریافت از کاربر است.
"""
from datetime import date, datetime

import jdatetime
from django.utils import timezone

_TO_FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
_TO_EN = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def fa_digits(value) -> str:
    return str(value).translate(_TO_FA)


def en_digits(value) -> str:
    return str(value).translate(_TO_EN)


def to_jalali(value, fa=True) -> str:
    """date یا datetime میلادی ← رشته‌ی شمسی «۱۴۰۵/۰۷/۳۰»."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        value = timezone.localtime(value).date() if timezone.is_aware(value) else value.date()
    j = jdatetime.date.fromgregorian(date=value)
    text = f"{j.year:04d}/{j.month:02d}/{j.day:02d}"
    return fa_digits(text) if fa else text


def parse_jalali(text) -> date:
    """رشته‌ی شمسی (با ارقام فارسی یا لاتین و جداکننده‌ی / یا -) ← date میلادی. ورودی نامعتبر: ValueError."""
    parts = en_digits(str(text).strip()).replace("-", "/").split("/")
    if len(parts) != 3:
        raise ValueError("قالب تاریخ نادرست است")
    year, month, day = (int(p) for p in parts)
    return jdatetime.date(year, month, day).togregorian()
