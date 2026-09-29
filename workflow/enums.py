"""
تمام مقادیر ثابت (Enum) سامانه در یک‌جا.
این فایل عمداً مستقل از مدل‌هاست تا ماشین‌حالت بدون پایگاه‌داده قابل‌آزمایش باشد.
"""
from django.db import models


class UserType(models.TextChoices):
    """نوع حساب کاربری (ویژگی خودِ کاربر، نه نقش او در یک پرونده)."""
    STUDENT = "student", "دانشجو"
    FACULTY = "faculty", "عضو هیئت‌علمی"
    EDU_STAFF = "edu_staff", "کارشناس آموزش دانشکده"
    EXTERNAL = "external", "نویسنده غیردانشجو"


class CaseRole(models.TextChoices):
    """نقش‌های زمینه‌ای؛ نقش هر کاربر نسبت به «یک پرونده‌ی مشخص» تعیین می‌شود."""
    AUTHOR = "author", "دانشجو / نویسنده"
    ADVISOR = "advisor", "استاد راهنما"
    HEAD = "head", "مدیر گروه"
    REFEREE = "referee", "داور"
    EDU = "edu", "آموزش دانشکده"


class State(models.TextChoices):
    ADVISOR_SELECTION = "advisor_selection", "انتخاب استاد راهنما"
    DRAFTING = "drafting", "تدوین پیشنهاد"
    ADVISOR_REVIEW = "advisor_review", "در انتظار تأیید استاد راهنما"
    EDU_REGISTRATION = "edu_registration", "در انتظار ثبت در آموزش دانشکده"
    REFEREE_ASSIGNMENT = "referee_assignment", "در انتظار تخصیص داور"
    REFEREE_REVIEW = "referee_review", "در حال داوری"
    REVISION = "revision", "نیازمند اصلاح توسط دانشجو"
    GROUP_APPROVAL = "group_approval", "در انتظار تصویب گروه"
    COUNCIL_APPROVAL = "council_approval", "در انتظار تصویب شورای دانشکده"
    IN_PROGRESS = "in_progress", "در حال اجرای پروژه"
    THESIS_ADVISOR_REVIEW = "thesis_advisor_review", "پایان‌نامه در انتظار تأیید استاد راهنما"
    THESIS_SUBMITTED = "thesis_submitted", "پایان‌نامه تحویل شد؛ در انتظار تعیین زمان دفاع"
    DEFENSE_SCHEDULED = "defense_scheduled", "زمان و مکان دفاع تعیین شد"
    COMPLETED = "completed", "تکمیل‌شده"
    REJECTED = "rejected", "ردشده"
    CANCELLED = "cancelled", "لغوشده"


class Semester(models.TextChoices):
    FIRST = "first", "نیم‌سال اول"
    SECOND = "second", "نیم‌سال دوم"
    SUMMER = "summer", "تابستان"


class DocKind(models.TextChoices):
    PROPOSAL = "proposal", "پیشنهاد پروژه"
    THESIS = "thesis", "پایان‌نامه"


class ReviewDecision(models.TextChoices):
    APPROVE = "approve", "تأیید"
    REVISE = "revise", "بازگشت جهت اصلاح"


class DeadlineKind(models.TextChoices):
    PROPOSAL_SUBMISSION = "proposal_submission", "تحویل پیشنهاد به آموزش دانشکده"
    GROUP_REVIEW = "group_review", "پایان داوری در گروه آموزشی"
    DEFENSE_EARLIEST = "defense_earliest", "زودترین تاریخ مجاز دفاع"
    THESIS_DELIVERY = "thesis_delivery", "تحویل پایان‌نامه (۳ هفته پیش از مهلت نمره)"
    GRADE_ENTRY = "grade_entry", "مهلت درج نمره"
