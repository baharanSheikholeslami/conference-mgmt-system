from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from workflow.enums import Semester, State, UserType
from workflow.models import Department, ProjectCase, RefereeAssignment, User

PASSWORD = "test1234"

# نام کاربری، نام، نام خانوادگی، نوع حساب، مدیر گروه؟، شماره دانشجویی، زمینه‌های تخصصی
USERS = [
    ("student1", "بهاران", "شیخ‌الاسلامی", UserType.STUDENT, False, "40031089", ""),
    ("external1", "نویسنده", "غیردانشجو", UserType.EXTERNAL, False, "", ""),
    ("advisor1", "سلیمان", "فلاح", UserType.FACULTY, False, "",
     "هوش مصنوعی، پردازش زبان طبیعی، عامل‌های مبتنی بر مدل زبانی بزرگ"),
    ("referee1", "ذاکری", "داور", UserType.FACULTY, False, "",
     "سامانه‌های اطلاعاتی، پایگاه‌داده، مهندسی نرم‌افزار"),
    ("referee2", "رضایی", "داور", UserType.FACULTY, False, "",
     "شبکه‌های کامپیوتری، رایانش ابری"),
    ("head1", "احمدی", "مدیر گروه", UserType.FACULTY, True, "",
     "معماری کامپیوتر، سیستم‌های نهفته"),
    ("edu1", "کارشناس", "آموزش", UserType.EDU_STAFF, False, "", ""),
]


class Command(BaseCommand):
    help = "ساخت گروه، کاربران و دو پرونده‌ی نمونه (رمز همه: test1234). چندبار اجرا کردن مشکلی ندارد."

    @transaction.atomic
    def handle(self, *args, **options):
        dept, _ = Department.objects.get_or_create(name="مهندسی کامپیوتر")
        users = {}
        for username, first, last, utype, is_head, number, expertise in USERS:
            has_dept = utype in (UserType.FACULTY,)
            u, created = User.objects.update_or_create(username=username, defaults={
                "first_name": first, "last_name": last, "user_type": utype,
                "department": dept if has_dept else None,
                "is_department_head": is_head, "student_number": number,
                "expertise": expertise,
            })
            if created:
                u.set_password(PASSWORD)
                u.save()
            users[username] = u

        today = timezone.localdate()
        base = dict(advisor=users["advisor1"], department=dept, academic_year=1405,
                    semester=Semester.FIRST, registration_date=today)

        case1, _ = ProjectCase.objects.get_or_create(
            author=users["student1"], academic_year=1405, semester=Semester.FIRST,
            defaults={**base, "state": State.DRAFTING,
                      "title_fa": "پیاده‌سازی سامانه هوشمند مدیریت کنفرانس مبتنی بر عامل‌های هوش مصنوعی",
                      "title_en": "Implementation of an Intelligent Conference Management System Based on AI Agents"})

        case2, _ = ProjectCase.objects.get_or_create(
            author=users["external1"], academic_year=1405, semester=Semester.FIRST,
            defaults={**base, "state": State.REFEREE_REVIEW, "edu_received_at": today,
                      "title_fa": "نمونه‌ی پرونده‌ی نویسنده غیردانشجو",
                      "title_en": "Sample non-student author case"})
        for ref in ("referee1", "referee2"):
            RefereeAssignment.objects.get_or_create(
                case=case2, referee=users[ref], defaults={"assigned_by": users["head1"]})

        for c in (case1, case2):
            c.sync_deadlines()

        self.stdout.write(self.style.SUCCESS("داده‌ی نمونه آماده است (رمز همه‌ی کاربران: test1234)."))