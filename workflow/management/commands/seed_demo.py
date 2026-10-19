from datetime import timedelta

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from workflow import rules
from workflow.enums import (DeadlineKind, DocKind, ReviewDecision, Semester, State,
                            UserType)
from workflow.models import (Department, DocumentVersion, ProjectCase, RefereeAssignment,
                             Review, User)

PASSWORD = "test1234"

# نام کاربری، نام، نام خانوادگی، نوع حساب، مدیر گروه؟، شماره دانشجویی، زمینه‌های تخصصی
USERS = [
    ("student1", "بهاران", "شیخ‌الاسلامی", UserType.STUDENT, False, "40031089", ""),
    ("student2", "دانشجوی", "دوم", UserType.STUDENT, False, "40031090", ""),   # بدون پرونده؛ برای آزمودن ثبت از صفر
    ("student3", "دانشجوی", "سوم", UserType.STUDENT, False, "40031091", ""),   # پرونده‌ی آماده‌ی ثبت نمره
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


# دانشجویانی که فقط با --full ساخته می‌شوند؛ هر کدام یک پرونده در یکی از وضعیت‌های میانی
# (نام کاربری، نام، شماره دانشجویی، وضعیت پرونده)
FULL_STUDENTS = [
    ("student4", "دانشجوی", "چهارم", "40031092", State.ADVISOR_REVIEW),
    ("student5", "دانشجوی", "پنجم", "40031093", State.EDU_REGISTRATION),
    ("student6", "دانشجوی", "ششم", "40031094", State.REFEREE_ASSIGNMENT),
    ("student7", "دانشجوی", "هفتم", "40031095", State.REVISION),
    ("student8", "دانشجوی", "هشتم", "40031096", State.GROUP_APPROVAL),
    ("student9", "دانشجوی", "نهم", "40031097", State.COUNCIL_APPROVAL),
    ("student10", "دانشجوی", "دهم", "40031098", State.IN_PROGRESS),
    ("student11", "دانشجوی", "یازدهم", "40031099", State.THESIS_ADVISOR_REVIEW),
    ("student12", "دانشجوی", "دوازدهم", "40031100", State.THESIS_SUBMITTED),
]


def sample_pdf(name="sample-proposal.pdf"):
    """یک PDF یک‌صفحه‌ای خالی و معتبر، تا پرونده‌های نمونه سندی برای داوری و دانلود داشته باشند."""
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (i, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return ContentFile(out, name=name)


class Command(BaseCommand):
    help = "ساخت گروه، کاربران و سه پرونده‌ی نمونه (رمز همه: test1234). چندبار اجرا کردن مشکلی ندارد."

    def add_arguments(self, parser):
        parser.add_argument("--full", action="store_true",
                            help="برای هر وضعیت میانی گردش‌کار یک دانشجو و پرونده‌ی آماده بسازد (student4 تا student12)")

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
                "email": f"{username}@example.com",     # برای آزمودن اعلان‌های ایمیلی (فاز ۳)
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

        if not case2.versions.exists():
            DocumentVersion.objects.create(case=case2, kind=DocKind.PROPOSAL, number=1,
                                           file=sample_pdf(), note="فایل نمونه",
                                           uploaded_by=users["external1"])

        for c in (case1, case2):
            c.sync_deadlines()
        # پرونده‌ی ۲ از مرحله‌ی «ثبت در آموزش» گذشته است؛ پس مهلت تحویل پیشنهادش انجام‌شده است
        case2.deadlines.filter(kind=DeadlineKind.PROPOSAL_SUBMISSION, satisfied_at__isnull=True).update(
            satisfied_at=case2.edu_received_at)

        self.seed_case_ready_for_grading(users, dept, today)
        if options["full"]:
            self.seed_cases_in_every_state(users, dept, today)

        self.stdout.write(self.style.SUCCESS("داده‌ی نمونه آماده است (رمز همه‌ی کاربران: test1234)."))

    def seed_case_ready_for_grading(self, users, dept, today):
        """
        پرونده‌ی ۳: همه‌ی مراحل را در گذشته طی کرده و روز دفاعش همین امروز است.
        چون نمره فقط از روز دفاع به بعد قابل‌ثبت است، با این پرونده می‌شود آخرین مرحله را آزمود (advisor1).
        """
        author = users["student3"]
        if ProjectCase.objects.filter(author=author).exists():
            return
        registered = today - timedelta(days=210)
        year, semester = rules.current_term(registered)
        case = ProjectCase.objects.create(
            author=author, advisor=users["advisor1"], department=dept,
            title_fa="نمونه‌ی پرونده‌ی آماده‌ی ثبت نمره", title_en="Sample case ready for grading",
            academic_year=year, semester=semester, registration_date=registered,
            state=State.DEFENSE_SCHEDULED,
            edu_received_at=registered + timedelta(days=30),
            final_approved_at=today - timedelta(days=120),
            thesis_submitted_at=today - timedelta(days=14),
            defense_date=today, defense_place="کلاس ۱۰۱ دانشکده")
        assignment = RefereeAssignment.objects.create(case=case, referee=users["referee1"],
                                                      assigned_by=users["head1"])
        proposal = DocumentVersion.objects.create(case=case, kind=DocKind.PROPOSAL, number=1,
                                                  file=sample_pdf(), note="فایل نمونه",
                                                  uploaded_by=author)
        Review.objects.create(assignment=assignment, version=proposal,
                              decision=ReviewDecision.APPROVE)
        DocumentVersion.objects.create(case=case, kind=DocKind.THESIS, number=1,
                                       file=sample_pdf("sample-thesis.pdf"), note="فایل نمونه",
                                       uploaded_by=author)
        case.sync_deadlines()
        for kind, done in ((DeadlineKind.PROPOSAL_SUBMISSION, case.edu_received_at),
                           (DeadlineKind.GROUP_REVIEW, case.final_approved_at),
                           (DeadlineKind.THESIS_DELIVERY, case.thesis_submitted_at)):
            case.deadlines.filter(kind=kind).update(satisfied_at=done)

    def seed_cases_in_every_state(self, users, dept, today):
        """
        برای هر وضعیتی که یک نقش در آن «کاری برای انجام» دارد، یک پرونده‌ی آماده می‌سازد
        تا هر نقش بدون طی‌کردن مسیر از ابتدا وارد شود و اقدام خودش را بیازماید.
        """
        year, semester = rules.current_term(today)
        for username, first, last, number, state in FULL_STUDENTS:
            author, created = User.objects.update_or_create(username=username, defaults={
                "first_name": first, "last_name": last, "user_type": UserType.STUDENT,
                "student_number": number, "email": f"{username}@example.com"})
            if created:
                author.set_password(PASSWORD)
                author.save()
            if ProjectCase.objects.filter(author=author).exists():
                continue
            self.seed_case(author, users, dept, today, state, year, semester)

    def seed_case(self, author, users, dept, today, state, year, semester):
        case = ProjectCase.objects.create(
            author=author, advisor=users["advisor1"], department=dept,
            title_fa=f"نمونه‌ی پرونده در وضعیت «{state.label}»", title_en=f"Sample case: {state.value}",
            academic_year=year, semester=semester, state=state,
            registration_date=today - timedelta(days=20))
        proposal = DocumentVersion.objects.create(case=case, kind=DocKind.PROPOSAL, number=1,
                                                  file=sample_pdf(), note="فایل نمونه", uploaded_by=author)
        order = list(State)
        reached = lambda s: order.index(state) >= order.index(s)     # noqa: E731
        if reached(State.REFEREE_ASSIGNMENT):
            case.edu_received_at = today - timedelta(days=10)
        if reached(State.IN_PROGRESS):
            case.final_approved_at = today - timedelta(days=3)
        if reached(State.THESIS_SUBMITTED):
            case.thesis_submitted_at = today - timedelta(days=1)
        case.save()

        if reached(State.REFEREE_REVIEW):
            assignment = RefereeAssignment.objects.create(case=case, referee=users["referee1"],
                                                          assigned_by=users["head1"])
            if reached(State.REVISION):
                revise = state == State.REVISION
                Review.objects.create(
                    assignment=assignment, version=proposal,
                    decision=ReviewDecision.REVISE if revise else ReviewDecision.APPROVE,
                    comment="لطفاً روش ارزیابی را دقیق‌تر توضیح دهید." if revise else "")
        if reached(State.THESIS_ADVISOR_REVIEW):
            DocumentVersion.objects.create(case=case, kind=DocKind.THESIS, number=1,
                                           file=sample_pdf("sample-thesis.pdf"), note="فایل نمونه",
                                           uploaded_by=author)
        case.sync_deadlines()
        done = {DeadlineKind.PROPOSAL_SUBMISSION: case.edu_received_at,
                DeadlineKind.GROUP_REVIEW: (today - timedelta(days=5)) if reached(State.COUNCIL_APPROVAL) else None,
                DeadlineKind.THESIS_DELIVERY: case.thesis_submitted_at}
        for kind, when in done.items():
            if when:
                case.deadlines.filter(kind=kind).update(satisfied_at=when)
