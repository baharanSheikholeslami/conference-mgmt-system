"""
قواعد و مهلت‌های آیین‌نامه‌ای (فرم پیشنهاد پروژه و رویه AUT-PR-3210).
همه‌ی اعداد این‌جا جمع شده‌اند تا اگر آیین‌نامه تغییر کرد فقط همین فایل ویرایش شود.
توابع این فایل «خالص» هستند (بدون پایگاه‌داده) و به‌سادگی آزمایش می‌شوند.
"""
import calendar
from datetime import date, timedelta

import jdatetime

from .enums import Semester

PROPOSAL_SUBMISSION_MONTHS = 2      # توضیح ۱: تحویل پیشنهاد حداکثر ۲ ماه پس از ثبت‌نام
GROUP_REVIEW_MONTHS = 2             # توضیح ۲: داوری گروه حداکثر ۲ ماه
MIN_MONTHS_APPROVAL_TO_DEFENSE = 3  # توضیح ۴: حداقل ۳ ماه بین تصویب نهایی و دفاع
THESIS_LEAD_WEEKS = 3               # توضیح ۴: پایان‌نامه حداقل ۳ هفته پیش از مهلت نمره
REFEREES_FOR_STUDENT = 1
REFEREES_FOR_NON_STUDENT = 2        # نویسنده غیردانشجو: حداقل دو داور
REMINDER_DAYS = (14, 7, 3, 1, 0)    # فاز ۳: چند روز مانده به هر مهلت یادآوری فرستاده شود (۰ = روز مهلت)
OVERDUE_REPEAT_DAYS = 7             # فاز ۳: پس از گذشتن مهلت، هشدار هر چند روز یک‌بار تکرار شود


def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    year = d.year + m // 12
    month = m % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def grade_entry_deadline(academic_year: int, semester: str) -> date:
    """
    توضیح ۳: نیم‌سال اول/تابستان ← ۳۰ مهر سال تحصیلی بعد؛ نیم‌سال دوم ← ۳۱ فروردین سال تحصیلی بعد.
    academic_year سال شمسی آغاز سال تحصیلی است (مثلاً ۱۴۰۴ برای مهر ۱۴۰۴ تا شهریور ۱۴۰۵).
    """
    if semester == Semester.SECOND:
        return jdatetime.date(academic_year + 2, 1, 31).togregorian()
    return jdatetime.date(academic_year + 1, 7, 30).togregorian()


def thesis_delivery_deadline(academic_year: int, semester: str) -> date:
    return grade_entry_deadline(academic_year, semester) - timedelta(weeks=THESIS_LEAD_WEEKS)


def current_term(today: date):
    """(سال تحصیلی، نیم‌سال) جاری بر اساس تقویم شمسی؛ پیش‌فرض فرم ثبت پرونده."""
    j = jdatetime.date.fromgregorian(date=today)
    if j.month >= 11:                       # بهمن و اسفند
        return j.year, Semester.SECOND
    if j.month >= 7:                        # مهر تا دی
        return j.year, Semester.FIRST
    if j.month >= 4:                        # تیر تا شهریور
        return j.year - 1, Semester.SUMMER
    return j.year - 1, Semester.SECOND      # فروردین تا خرداد


def reminder_stage(days_left: int):
    """
    مرحله‌ی یادآوری یک مهلت بر اساس روزهای مانده؛ None یعنی هنوز زود است.
    هر مرحله برای هر گیرنده فقط یک بار فرستاده می‌شود، پس اگر سامانه یک روز خاموش باشد
    یادآوری همان مرحله روز بعد جبران می‌شود:
        ۸ تا ۱۴ روز مانده ← d14 | ۴ تا ۷ ← d7 | ۲ و ۳ ← d3 | ۱ ← d1 | روز مهلت ← d0
        پس از مهلت ← overdue1 (هفته‌ی اول تأخیر)، overdue2 (هفته‌ی دوم)، …
    """
    if days_left < 0:
        return f"overdue{(-days_left - 1) // OVERDUE_REPEAT_DAYS + 1}"
    for threshold in sorted(REMINDER_DAYS):
        if days_left <= threshold:
            return f"d{threshold}"
    return None
