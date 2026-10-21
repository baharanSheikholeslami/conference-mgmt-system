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

# کلید داخلی، نام کاربری، نام، نام خانوادگی، نوع حساب، مدیر گروه؟، شماره دانشجویی، زمینه‌های تخصصی
# (کلید داخلی فقط برای ارجاع در همین فایل است؛ کاربر با «نام کاربری» وارد می‌شود)
USERS = [
    ("student1", "baharan.sheikholeslami", "بهاران", "شیخ‌الاسلامی", UserType.STUDENT, False, "40031089", ""),
    ("student2", "sara.nouri", "سارا", "نوری", UserType.STUDENT, False, "40031120", ""),   # بدون پرونده؛ برای مسیر کامل از صفر
    ("student3", "amir.hosseini", "امیر", "حسینی", UserType.STUDENT, False, "40031091", ""),   # پرونده‌ی آماده‌ی ثبت نمره
    ("external1", "kamran.shirazi", "کامران", "شیرازی", UserType.EXTERNAL, False, "", ""),
    ("advisor1", "soleiman.fallah", "سلیمان", "فلاح", UserType.FACULTY, False, "",
     "هوش مصنوعی، پردازش زبان طبیعی، عامل‌های مبتنی بر مدل زبانی بزرگ"),
    ("referee1", "mehdi.zakeri", "مهدی", "ذاکری", UserType.FACULTY, False, "",
     "سامانه‌های اطلاعاتی، پایگاه‌داده، مهندسی نرم‌افزار"),
    ("referee2", "narges.rezaei", "نرگس", "رضایی", UserType.FACULTY, False, "",
     "شبکه‌های کامپیوتری، رایانش ابری"),
    ("referee3", "parisa.karimi", "پریسا", "کریمی", UserType.FACULTY, False, "",
     "امنیت شبکه، رمزنگاری، امنیت اینترنت اشیا"),
    ("referee4", "hossein.mousavi", "حسین", "موسوی", UserType.FACULTY, False, "",
     "یادگیری عمیق، بینایی ماشین، پردازش تصویر"),
    ("head1", "ali.ahmadi", "علی", "احمدی", UserType.FACULTY, True, "",
     "معماری کامپیوتر، سیستم‌های نهفته"),
    ("edu1", "maryam.salehi", "مریم", "صالحی", UserType.EDU_STAFF, False, "", ""),
]

# نام‌های کاربری نسخه‌های قبلی این داده‌ی نمونه؛ اگر هنوز در پایگاه‌داده باشند، همان کاربر (و پرونده‌اش) تغییر نام می‌دهد
LEGACY_USERNAMES = {
    "student1": "baharan.sheikholeslami", "student2": "sara.nouri", "student3": "amir.hosseini",
    "external1": "kamran.shirazi", "advisor1": "soleiman.fallah", "referee1": "mehdi.zakeri",
    "referee2": "narges.rezaei", "head1": "ali.ahmadi", "edu1": "maryam.salehi",
    "student4": "mahsa.rahimi", "student5": "reza.sadeghi", "student6": "niloofar.kazemi",
    "student7": "pouria.abbasi", "student8": "elaheh.jafari", "student9": "mohammad.taheri",
    "student10": "fatemeh.mousavi", "student11": "alireza.niknam", "student12": "zahra.babaei",
}

# عنوان پروژه‌ی پرونده‌های پایه (کلید داخلی دانشجو ← (عنوان فارسی، عنوان انگلیسی))
BASE_TITLES = {
    "student1": ("پیاده‌سازی سامانه هوشمند مدیریت کنفرانس مبتنی بر عامل‌های هوش مصنوعی",
                 "Implementation of an Intelligent Conference Management System Based on AI Agents"),
    "external1": ("مقایسه روش‌های ارزیابی سامانه‌های مدیریت همایش علمی",
                  "A Comparison of Evaluation Methods for Scientific Conference Management Systems"),
    "student3": ("طراحی موتور جست‌وجوی معنایی برای اسناد فارسی",
                 "Design of a Semantic Search Engine for Persian Documents"),
}

# دانشجویانی که فقط با --full ساخته می‌شوند؛ هر کدام یک پرونده در یکی از وضعیت‌های میانی
# (نام کاربری، نام، نام خانوادگی، شماره دانشجویی، وضعیت پرونده، عنوان فارسی، عنوان انگلیسی)
FULL_STUDENTS = [
    ("mahsa.rahimi", "مهسا", "رحیمی", "40031092", State.ADVISOR_REVIEW,
     "تشخیص خودکار اخبار جعلی فارسی با مدل‌های زبانی", "Automatic Detection of Persian Fake News with Language Models"),
    ("reza.sadeghi", "رضا", "صادقی", "40031093", State.EDU_REGISTRATION,
     "طراحی سامانه توصیه‌گر فیلم مبتنی بر یادگیری عمیق", "A Deep-Learning Based Movie Recommender System"),
    ("niloofar.kazemi", "نیلوفر", "کاظمی", "40031094", State.REFEREE_ASSIGNMENT,
     "پیاده‌سازی سامانه مدیریت کتابخانه با پایگاه‌داده‌ی PostgreSQL",
     "A Library Management System on PostgreSQL"),
    ("pouria.abbasi", "پوریا", "عباسی", "40031095", State.REVISION,
     "تحلیل امنیت پروتکل‌های ارتباطی اینترنت اشیا", "Security Analysis of Internet of Things Communication Protocols"),
    ("elaheh.jafari", "الهه", "جعفری", "40031096", State.GROUP_APPROVAL,
     "سامانه تشخیص چهره برای حضور و غیاب خودکار کلاس‌ها",
     "A Face-Recognition System for Automatic Class Attendance"),
    ("mohammad.taheri", "محمد", "طاهری", "40031097", State.COUNCIL_APPROVAL,
     "بهینه‌سازی مصرف انرژی در سامانه‌های نهفته", "Energy Consumption Optimization in Embedded Systems"),
    ("fatemeh.mousavi", "فاطمه", "موسوی", "40031098", State.IN_PROGRESS,
     "چت‌بات پاسخ‌گوی دانشجویان بر پایه بازیابی-افزوده تولید", "A Retrieval-Augmented Chatbot for Student Questions"),
    ("alireza.niknam", "علیرضا", "نیک‌نام", "40031099", State.THESIS_ADVISOR_REVIEW,
     "ابزار تحلیل ایستای کد برای یافتن آسیب‌پذیری برنامه‌های وب",
     "A Static Analysis Tool for Finding Vulnerabilities in Web Applications"),
    ("zahra.babaei", "زهرا", "بابایی", "40031100", State.THESIS_SUBMITTED,
     "برنامه‌ی موبایل رزرو نوبت درمانگاه", "A Mobile App for Clinic Appointment Booking"),
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
                            help="برای هر وضعیت میانی گردش‌کار یک دانشجو و پرونده‌ی آماده بسازد (نُه دانشجوی دیگر با پرونده در وضعیت‌های میانی)")

    @transaction.atomic
    def handle(self, *args, **options):
        dept, _ = Department.objects.get_or_create(name="مهندسی کامپیوتر")
        users = {}
        for alias, username, first, last, utype, is_head, number, expertise in USERS:
            has_dept = utype in (UserType.FACULTY,)
            self.rename_legacy(username)
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
            users[alias] = u

        today = timezone.localdate()
        base = dict(advisor=users["advisor1"], department=dept, academic_year=1405,
                    semester=Semester.FIRST, registration_date=today)

        case1, _ = ProjectCase.objects.get_or_create(
            author=users["student1"], academic_year=1405, semester=Semester.FIRST,
            defaults={**base, "state": State.DRAFTING,
                      "title_fa": BASE_TITLES["student1"][0], "title_en": BASE_TITLES["student1"][1]})

        case2, _ = ProjectCase.objects.get_or_create(
            author=users["external1"], academic_year=1405, semester=Semester.FIRST,
            defaults={**base, "state": State.REFEREE_REVIEW, "edu_received_at": today,
                      "title_fa": BASE_TITLES["external1"][0], "title_en": BASE_TITLES["external1"][1]})
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
        for alias, (fa, en) in BASE_TITLES.items():     # پایگاه‌داده‌ی قدیمی با عنوان‌های قبلی هم به‌روز شود
            ProjectCase.objects.filter(author=users[alias]).update(title_fa=fa, title_en=en)
        if options["full"]:
            self.seed_cases_in_every_state(users, dept, today)

        self.stdout.write(self.style.SUCCESS("داده‌ی نمونه آماده است (رمز همه‌ی کاربران: test1234)."))

    @staticmethod
    def rename_legacy(username):
        """نام کاربری قدیمی (مثلاً student1) را، اگر هنوز هست، به نام جدید برمی‌گرداند تا کاربر و پرونده‌اش دو بار ساخته نشوند."""
        old = next((k for k, v in LEGACY_USERNAMES.items() if v == username), None)
        if old and not User.objects.filter(username=username).exists():
            User.objects.filter(username=old).update(username=username)

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
            title_fa=BASE_TITLES["student3"][0], title_en=BASE_TITLES["student3"][1],
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
        for username, first, last, number, state, title_fa, title_en in FULL_STUDENTS:
            self.rename_legacy(username)
            author, created = User.objects.update_or_create(username=username, defaults={
                "first_name": first, "last_name": last, "user_type": UserType.STUDENT,
                "student_number": number, "email": f"{username}@example.com"})
            if created:
                author.set_password(PASSWORD)
                author.save()
            case = ProjectCase.objects.filter(author=author).first()
            if case:
                ProjectCase.objects.filter(pk=case.pk).update(title_fa=title_fa, title_en=title_en)
                continue
            self.seed_case(author, users, dept, today, state, year, semester, title_fa, title_en)

    def seed_case(self, author, users, dept, today, state, year, semester, title_fa, title_en):
        case = ProjectCase.objects.create(
            author=author, advisor=users["advisor1"], department=dept,
            title_fa=title_fa, title_en=title_en,
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
