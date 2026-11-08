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
GROUP_REVIEW_MONTHS = 2             # توضیح ۲: داوری پیشنهاد (در گروه) حداکثر ۲ ماه
MAX_ADVISEES = 2                    # هر استاد هم‌زمان حداکثر راهنمای این تعداد دانشجو (پروژه‌ی جاری) است
MIN_MONTHS_APPROVAL_TO_DEFENSE = 3  # توضیح ۴: حداقل ۳ ماه بین تصویب نهایی و دفاع
THESIS_LEAD_WEEKS = 3               # توضیح ۴: پایان‌نامه حداقل ۳ هفته پیش از مهلت نمره
REFEREES_FOR_STUDENT = 1
REFEREES_FOR_NON_STUDENT = 2        # نویسنده غیردانشجو: حداقل دو داور
# درخواست دفاع: دانشجو چند روز پیشنهاد می‌دهد و استاد راهنما و داور(ان) از میان ساعت‌های همان روزها
# زمان‌های آزاد خود را انتخاب می‌کنند؛ سامانه نخستین زمانی را که برای همه آزاد است روز دفاع می‌کند.
DEFENSE_MIN_DAYS = 2                # حداقل روزهای پیشنهادی در یک درخواست دفاع
DEFENSE_MAX_DAYS = 5                # حداکثر روزهای پیشنهادی
DEFENSE_HORIZON_DAYS = 60           # روزهای پیشنهادی حداکثر این تعداد روز پس از زودترین تاریخ ممکن باشند
DEFENSE_TIME_SLOTS = ("09:00", "11:00", "14:00", "16:00")   # ساعت‌های شروع جلسه‌ی دفاع در هر روز
NO_DEFENSE_WEEKDAYS = (4,)          # جمعه (weekday() پایتون: دوشنبه=۰ … جمعه=۴)
REMINDER_DAYS = (14, 7, 3, 1, 0)    # فاز ۳: چند روز مانده به هر مهلت یادآوری فرستاده شود (۰ = روز مهلت)
OVERDUE_REPEAT_DAYS = 7             # فاز ۳: پس از گذشتن مهلت، هشدار هر چند روز یک‌بار تکرار شود


def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    year = d.year + m // 12
    month = m % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


WEEKDAYS_FA = ("دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه")


def weekday_fa(d: date) -> str:
    return WEEKDAYS_FA[d.weekday()]


def earliest_defense_day(final_approved_at: date, today: date) -> date:
    """زودترین روزی که دانشجو می‌تواند برای دفاع پیشنهاد دهد: امروز به بعد، و نه پیش از تصویب + ۳ ماه."""
    if final_approved_at is None:
        return today
    return max(today, add_months(final_approved_at, MIN_MONTHS_APPROVAL_TO_DEFENSE))


def slot_key(day: date, time: str) -> str:
    """کلید یک زمان پیشنهادی، مثل 2027-01-10T09:00 (ترتیب الفبایی همان ترتیب زمانی است)."""
    return f"{day.isoformat()}T{time}"


def first_common_slot(choices):
    """
    choices: فهرستی از مجموعه‌ی زمان‌های انتخاب‌شده‌ی هر استاد (کلیدهای slot_key).
    خروجی: زودترین زمانی که همه انتخاب کرده‌اند، یا None اگر زمان مشترکی نیست.
    """
    choices = [set(c) for c in choices]
    if not choices:
        return None
    common = set.intersection(*choices)
    return min(common) if common else None


def countdown(due: date, today: date):
    """
    فاصله‌ی تقویمی «امروز» تا یک مهلت به‌صورت (ماه، روز، گذشته؟) برای نمایش تایمر.
    ماه‌ها با add_months شمرده می‌شوند، پس «یک ماه» همان روز ماه بعد است نه ۳۰ روز.
    """
    overdue = due < today
    start, end = (due, today) if overdue else (today, due)
    months = 0
    while add_months(start, months + 1) <= end:
        months += 1
    return months, (end - add_months(start, months)).days, overdue


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
