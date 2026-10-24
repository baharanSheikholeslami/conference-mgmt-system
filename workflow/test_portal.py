"""آزمون‌های پورتال (ورود، داشبورد، فرم‌ها و کنترل دسترسی) با کلاینت آزمون جنگو."""
from datetime import timedelta
from pathlib import Path
from io import StringIO

from django.test import TestCase, override_settings
from django.urls import reverse

from . import rules, services
from .enums import DocKind, ReviewDecision, State
from .jalali import parse_jalali, to_jalali
from .models import ProjectCase, User
from .test_services import MEDIA, WorkflowTestCase, pdf


@override_settings(MEDIA_ROOT=MEDIA)
class PortalTestCase(WorkflowTestCase):
    def login(self, user):
        self.client.force_login(user)

    def post_action(self, case, action, **data):
        return self.client.post(reverse("case_action", args=[case.pk, action]), data)


class AuthTests(PortalTestCase):
    def test_every_page_requires_login(self):
        case = self.new_case()
        for url in (reverse("dashboard"), reverse("case_create"),
                    reverse("case_detail", args=[case.pk]),
                    reverse("case_action", args=[case.pk, "select_advisor"])):
            response = self.client.get(url)
            self.assertRedirects(response, f"{reverse('login')}?next={url}")

    def test_login_with_password_then_logout(self):
        response = self.client.post(reverse("login"), {"username": "student", "password": "pass12345"})
        self.assertRedirects(response, reverse("dashboard"))
        self.assertRedirects(self.client.post(reverse("logout")), reverse("login"))
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 302)

    def test_user_can_change_own_password(self):
        self.login(self.student)
        self.assertContains(self.client.get(reverse("dashboard")), reverse("password_change"))
        response = self.client.post(reverse("password_change"), {
            "old_password": "pass12345", "new_password1": "Ramz-e-taze-1405",
            "new_password2": "Ramz-e-taze-1405"})
        self.assertRedirects(response, reverse("dashboard"))
        self.client.post(reverse("logout"))
        self.assertFalse(self.client.login(username="student", password="pass12345"))
        self.assertTrue(self.client.login(username="student", password="Ramz-e-taze-1405"))

    def test_unrelated_user_gets_404_for_case_and_file(self):
        case = self.to_referee_review()
        version = case.versions.get()
        for user, expected in ((self.outsider, 404), (self.ref2, 404), (self.ref1, 200), (self.edu, 200)):
            self.login(user)
            self.assertEqual(self.client.get(reverse("case_detail", args=[case.pk])).status_code,
                             expected, user.username)
            self.assertEqual(
                self.client.get(reverse("document_download", args=[version.pk])).status_code,
                expected, user.username)

    def test_user_without_the_role_cannot_open_or_post_an_action(self):
        case = self.to_referee_review()
        self.login(self.student)                       # نویسنده پرونده را می‌بیند ولی داور نیست
        self.assertEqual(self.client.get(reverse("case_action", args=[case.pk, "submit_review"])).status_code, 403)
        self.assertEqual(self.post_action(case, "submit_review", decision="approve").status_code, 403)
        case.refresh_from_db()
        self.assertEqual(case.state, State.REFEREE_REVIEW)

    def test_only_authors_see_the_create_form(self):
        self.login(self.advisor)
        self.assertEqual(self.client.get(reverse("case_create")).status_code, 403)
        self.assertNotContains(self.client.get(reverse("dashboard")), "ثبت پرونده‌ی جدید")


class DashboardTests(PortalTestCase):
    def test_each_role_sees_the_case_as_pending_only_when_it_is_their_turn(self):
        case = self.to_referee_review()                # نوبت داور ۱ است
        expectations = {self.ref1: (1, 0), self.student: (0, 1), self.advisor: (0, 1),
                        self.head: (0, 1), self.edu: (0, 1), self.outsider: (0, 0)}
        for user, (pending, others) in expectations.items():
            self.login(user)
            context = self.client.get(reverse("dashboard")).context
            self.assertEqual((len(context["pending"]), len(context["others"])),
                             (pending, others), user.username)

        services.perform(case, "submit_review", self.ref1, decision=ReviewDecision.APPROVE)
        self.login(self.edu)                           # حالا نوبت کارشناس آموزش (شورای دانشکده) است
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(len(response.context["pending"]), 1)
        self.assertContains(response, "تصویب در شورای دانشکده و ثبت نهایی")

    def test_dashboard_shows_nearest_deadline_in_jalali(self):
        case = self.new_case()
        self.login(self.student)
        due = rules.add_months(case.registration_date, 2)
        self.assertContains(self.client.get(reverse("dashboard")), to_jalali(due))


class FormFlowTests(PortalTestCase):
    def test_student_creates_case_selects_advisor_uploads_and_submits(self):
        self.login(self.student)
        response = self.client.post(reverse("case_create"), {
            "title_fa": "سامانه‌ی آزمایشی", "title_en": "Test system",
            "academic_year": 1405, "semester": "first"})
        case = ProjectCase.objects.get()
        self.assertRedirects(response, reverse("case_detail", args=[case.pk]))

        self.assertContains(self.client.get(reverse("case_action", args=[case.pk, "select_advisor"])),
                            str(self.advisor))
        self.post_action(case, "select_advisor", advisor=self.advisor.pk)
        case.refresh_from_db()
        self.assertEqual((case.state, case.advisor), (State.ADVISOR_PENDING, self.advisor))
        self.assertContains(self.client.get(reverse("case_detail", args=[case.pk])),
                            "در انتظار پذیرش استاد راهنما")
        self.login(self.advisor)                       # استاد باید درخواست را بپذیرد
        self.assertEqual(len(self.client.get(reverse("dashboard")).context["pending"]), 1)
        # پذیرش و رد کنار هم‌اند و نباید هم‌رنگ باشند
        detail = self.client.get(reverse("case_detail", args=[case.pk]))
        self.assertContains(detail, 'class="btn approve"')
        self.assertContains(detail, 'class="btn reject"')
        self.assertContains(self.client.get(reverse("case_action", args=[case.pk, "advisor_decline"])),
                            'class="btn reject solid"')
        self.post_action(case, "advisor_accept")
        case.refresh_from_db()
        self.assertEqual(case.state, State.DRAFTING)
        self.login(self.student)

        # تا فایل بارگذاری نشده، دکمه‌ی ارسال غیرفعال است و دلیلش دیده می‌شود
        self.assertContains(self.client.get(reverse("case_detail", args=[case.pk])),
                            "ابتدا فایل پیشنهاد را بارگذاری کنید.")
        response = self.client.post(reverse("document_upload", args=[case.pk]),
                                    {"file": pdf(), "note": "نسخه‌ی اول"})
        self.assertRedirects(response, reverse("case_detail", args=[case.pk]))
        self.post_action(case, "submit_proposal")
        case.refresh_from_db()
        self.assertEqual(case.state, State.ADVISOR_REVIEW)

    def test_upload_rejects_wrong_file_type(self):
        case = self.choose_advisor(self.new_case())
        self.login(self.student)
        response = self.client.post(reverse("document_upload", args=[case.pk]),
                                    {"file": pdf("virus.exe")})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(case.versions.exists())

    def test_required_note_is_enforced_by_the_form(self):
        case = self.choose_advisor(self.new_case())
        services.upload_document(case, self.student, DocKind.PROPOSAL, pdf())
        case = services.perform(case, "submit_proposal", self.student)
        self.login(self.advisor)
        response = self.post_action(case, "advisor_return", note="")
        self.assertEqual(response.status_code, 200)                 # فرم با خطا دوباره نمایش داده می‌شود
        self.assertTrue(response.context["form"].errors["note"])
        self.assertRedirects(self.post_action(case, "advisor_return", note="اهداف را دقیق‌تر بنویسید"),
                             reverse("case_detail", args=[case.pk]))
        self.assertContains(self.client.get(reverse("case_detail", args=[case.pk])),
                            "اهداف را دقیق‌تر بنویسید")

    def test_head_assigns_and_referee_reviews_through_forms(self):
        case = self.to_referee_review()
        case.state = State.REFEREE_ASSIGNMENT
        case.save()
        case.referee_assignments.all().delete()

        self.login(self.head)
        page = self.client.get(reverse("case_action", args=[case.pk, "assign_referees"]))
        choices = set(page.context["form"].fields["referees"].queryset)
        self.assertEqual(choices, {self.ref1, self.ref2, self.head})   # نه راهنما، نه گروه دیگر
        self.post_action(case, "assign_referees", referees=[self.ref2.pk])

        self.login(self.ref2)
        response = self.post_action(case, "submit_review", decision="revise", note="")
        self.assertContains(response, "نظر مکتوب الزامی است")           # خطای سرویس روی فرم
        self.post_action(case, "submit_review", decision="revise", note="منابع ناقص است")
        case.refresh_from_db()
        self.assertEqual(case.state, State.REVISION)
        self.login(self.student)
        self.assertContains(self.client.get(reverse("case_detail", args=[case.pk])), "منابع ناقص است")

    def test_defense_date_is_entered_in_jalali_and_checked(self):
        case = self.to_in_progress()
        services.upload_document(case, self.student, DocKind.THESIS, pdf())
        case = services.perform(case, "submit_thesis", self.student)
        case = services.perform(case, "thesis_approve", self.advisor)
        earliest = rules.add_months(case.final_approved_at, 3)

        self.login(self.edu)
        too_early = self.post_action(case, "schedule_defense", defense_place="کلاس ۲۰۲",
                                     defense_date=to_jalali(case.final_approved_at))
        self.assertContains(too_early, "زودترین تاریخ مجاز")
        bad_format = self.post_action(case, "schedule_defense", defense_place="کلاس ۲۰۲",
                                      defense_date="2027-01-10x")
        self.assertTrue(bad_format.context["form"].errors["defense_date"])

        self.post_action(case, "schedule_defense", defense_place="کلاس ۲۰۲",
                         defense_date=to_jalali(earliest))            # با ارقام فارسی
        case.refresh_from_db()
        self.assertEqual((case.state, case.defense_date), (State.DEFENSE_SCHEDULED, earliest))

        # تا روز دفاع: دکمه‌ی نمره غیرفعال است و پرونده در «در انتظار اقدام» استاد راهنما نیست
        self.login(self.advisor)
        self.assertContains(self.client.get(reverse("case_detail", args=[case.pk])),
                            "ثبت نتیجه پیش از روز دفاع")
        self.assertEqual(self.client.get(reverse("dashboard")).context["pending"], [])
        self.post_action(case, "record_result", grade="17.75")
        case.refresh_from_db()
        self.assertEqual(case.state, State.DEFENSE_SCHEDULED)

        with self.on(earliest):                                       # روز دفاع
            self.assertEqual(len(self.client.get(reverse("dashboard")).context["pending"]), 1)
            self.assertContains(self.post_action(case, "record_result", grade="25"), "errorlist")
            self.post_action(case, "record_result", grade="17.75")
        case.refresh_from_db()
        self.assertEqual((case.state, str(case.grade)), (State.COMPLETED, "17.75"))

    def test_author_edits_title_only_while_drafting(self):
        case = self.new_case()
        self.login(self.student)
        self.client.post(reverse("case_edit", args=[case.pk]), {"title_fa": "عنوان تازه", "title_en": ""})
        case.refresh_from_db()
        self.assertEqual(case.title_fa, "عنوان تازه")
        case = self.to_referee_review(author=self.external, referees=[self.ref1, self.ref2])
        self.login(self.external)
        self.assertEqual(self.client.get(reverse("case_edit", args=[case.pk])).status_code, 403)

    def test_edu_can_cancel_with_a_reason(self):
        case = self.new_case()
        self.login(self.edu)
        self.post_action(case, "cancel", note="انصراف دانشجو")
        case.refresh_from_db()
        self.assertEqual(case.state, State.CANCELLED)


class JalaliTests(PortalTestCase):
    def test_round_trip_and_persian_digits(self):
        d = parse_jalali("۱۴۰۵/۰۷/۳۰")
        self.assertEqual(d.isoformat(), "2026-10-22")
        self.assertEqual(to_jalali(d), "۱۴۰۵/۰۷/۳۰")
        self.assertEqual(parse_jalali("1405-7-30"), d)
        for bad in ("1405/13/01", "abc", "1405/07", ""):
            with self.assertRaises(ValueError):
                parse_jalali(bad)


@override_settings(MEDIA_ROOT=MEDIA)
class SeedDemoFullTests(TestCase):
    """seed_demo --full: برای هر وضعیت میانی یک پرونده‌ی آماده، و اجرای دوباره بی‌اثر است."""

    def run_seed(self, *args):
        from django.core.management import call_command
        call_command("seed_demo", *args, stdout=StringIO())

    def test_full_seed_covers_every_waiting_state_and_is_idempotent(self):
        self.run_seed()
        base = ProjectCase.objects.count()
        self.run_seed("--full")
        self.run_seed("--full")
        states = set(ProjectCase.objects.values_list("state", flat=True))
        self.assertEqual(ProjectCase.objects.count(), base + 9)
        self.assertEqual(states, set(State) - {State.ADVISOR_SELECTION, State.COMPLETED,
                                                 State.REJECTED, State.CANCELLED})

    def test_seeded_advisors_respect_the_capacity_and_the_request_is_pending(self):
        from workflow import services
        self.run_seed("--full")
        for advisor in User.objects.filter(user_type="faculty"):
            self.assertLessEqual(services.advisee_count(advisor), rules.MAX_ADVISEES, advisor.username)
        sharifi = User.objects.get(username="leila.sharifi")
        self.assertTrue(services.advisor_has_room(sharifi))
        self.assertFalse(services.advisor_has_room(User.objects.get(username="soleiman.fallah")))
        request = ProjectCase.objects.get(author__username="elaheh.jafari")
        self.assertEqual((request.state, request.advisor), (State.ADVISOR_PENDING, sharifi))
        self.assertTrue(services.is_pending_for(request, sharifi))
        self.assertFalse(request.versions.exists())
        revision = ProjectCase.objects.get(author__username="pouria.abbasi")
        notes = [i["note"] for i in services.pending_feedback(revision)["items"]]
        self.assertEqual(notes, ["لطفاً روش ارزیابی را دقیق‌تر توضیح دهید."])

    def test_every_seeded_case_offers_its_role_an_action(self):
        from workflow import services
        self.run_seed("--full")
        edu, head = User.objects.get(username="maryam.salehi"), User.objects.get(username="ali.ahmadi")
        ref = User.objects.get(username="mehdi.zakeri")
        for case in ProjectCase.objects.exclude(author__username__in=["baharan.sheikholeslami", "kamran.shirazi"]):
            actors = [case.author, case.advisor, head, edu, ref]
            todo = [u.username for u in actors
                    if any(o.pending for o in services.available_actions(case, u))]
            self.assertTrue(todo, f"{case.state}: هیچ نقشی کاری ندارد")


@override_settings(MEDIA_ROOT=MEDIA, DEBUG=True)
class BackdateCaseTests(TestCase):
    """backdate_case: پس از تصویب نهایی، دفاع در همان روز قابل‌زمان‌بندی و ثبت نمره است."""

    def test_backdating_unlocks_same_day_defense_and_grading(self):
        from django.core.management import CommandError, call_command
        from django.utils import timezone
        call_command("seed_demo", "--full", stdout=StringIO())
        case = ProjectCase.objects.get(author__username="fatemeh.mousavi")   # در حال اجرای پروژه
        today = timezone.localdate()
        self.assertEqual(case.final_approved_at, today - timedelta(days=3))
        call_command("backdate_case", str(case.pk), "--days", "120", stdout=StringIO())
        case.refresh_from_db()
        self.assertEqual(case.final_approved_at, today - timedelta(days=123))
        with override_settings(DEBUG=False), self.assertRaises(CommandError):
            call_command("backdate_case", str(case.pk), stdout=StringIO())


@override_settings(MEDIA_ROOT=MEDIA, DEBUG=True)
class GuideScenarioTests(TestCase):
    """
    همان سناریوی کامل «سارا نوری» در docs/test-guide-fa.md، با کلاینت وب و فایل‌های docs/samples،
    از ثبت پرونده تا نمره. اگر راهنما و برنامه از هم جدا شوند، این آزمون می‌شکند.
    """

    SAMPLES = Path(__file__).resolve().parent.parent / "docs" / "samples"

    def act(self, username, case, action, **data):
        self.client.force_login(User.objects.get(username=username))
        response = self.client.post(reverse("case_action", args=[case.pk, action]), data)
        self.assertEqual(response.status_code, 302, f"{username}/{action}: {response.content.decode()[:300]}")

    def upload(self, username, case, filename):
        self.client.force_login(User.objects.get(username=username))
        with open(self.SAMPLES / filename, "rb") as f:
            response = self.client.post(reverse("document_upload", args=[case.pk]), {"file": f})
        self.assertEqual(response.status_code, 302, response.content.decode()[:300])

    def test_full_flow_from_scratch_to_grade(self):
        from django.core.management import call_command
        from django.utils import timezone
        call_command("seed_demo", stdout=StringIO())
        sara = User.objects.get(username="sara.nouri")
        sharifi = User.objects.get(username="leila.sharifi")
        mousavi = User.objects.get(username="hossein.mousavi")

        self.client.force_login(sara)
        self.client.post(reverse("case_create"), {
            "title_fa": "دستیار هوشمند پاسخ‌گویی به آیین‌نامه‌های آموزشی با مدل‌های زبانی بزرگ",
            "title_en": "An LLM-based Assistant for Answering Academic Regulation Questions",
            "academic_year": rules.current_term(timezone.localdate())[0],
            "semester": rules.current_term(timezone.localdate())[1]})
        case = ProjectCase.objects.get(author=sara)
        self.assertEqual(case.state, State.ADVISOR_SELECTION)

        self.act("sara.nouri", case, "select_advisor", advisor=sharifi.pk)
        self.act("leila.sharifi", case, "advisor_accept")
        self.upload("sara.nouri", case, "proposal-sara-nouri.docx")
        self.act("sara.nouri", case, "submit_proposal")
        self.act("leila.sharifi", case, "advisor_approve")
        self.act("maryam.salehi", case, "edu_register")
        self.act("ali.ahmadi", case, "assign_referees", referees=[mousavi.pk])
        self.act("hossein.mousavi", case, "submit_review", decision="approve")
        self.act("maryam.salehi", case, "council_approve")
        case.refresh_from_db()
        self.assertEqual(case.state, State.IN_PROGRESS)

        self.upload("sara.nouri", case, "thesis-sara-nouri.docx")
        self.act("sara.nouri", case, "submit_thesis")
        self.act("leila.sharifi", case, "thesis_approve")

        call_command("backdate_case", str(case.pk), "--days", "120", stdout=StringIO())
        self.act("maryam.salehi", case, "schedule_defense",
                 defense_date=to_jalali(timezone.localdate(), fa=False), defense_place="کلاس ۲۰۱ دانشکده")
        self.act("leila.sharifi", case, "record_result", grade="18.5")
        case.refresh_from_db()
        self.assertEqual((case.state, float(case.grade)), (State.COMPLETED, 18.5))


class DashboardRemindersTests(PortalTestCase):
    def test_advisor_and_referee_get_a_reminder_card_and_see_their_students(self):
        case = self.to_referee_review()
        self.login(self.ref1)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual([r["case"] for r in response.context["reminders"]], [case])
        self.assertContains(response, "منتظر بررسی و تأیید شماست")
        self.assertContains(response, "ثبت رأی داور")
        self.assertEqual([r.case for r in response.context["refereeing"]], [case])
        self.assertContains(response, str(self.student))           # نام دانشجو

        self.login(self.advisor)                                    # راهنما فعلاً کاری ندارد، اما دانشجویش را می‌بیند
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.context["reminders"], [])
        self.assertEqual([r.case for r in response.context["advisees"]], [case])
        self.assertContains(response, str(self.student))
        self.assertEqual(response.context["capacity"], {"used": 1, "max": rules.MAX_ADVISEES})

    def test_advisor_reminder_lists_requests_and_proposals_awaiting_approval(self):
        request = self.do(self.new_case(), "select_advisor", self.student, advisor=self.advisor)
        self.login(self.advisor)
        response = self.client.get(reverse("dashboard"))
        reminder = response.context["reminders"][0]
        self.assertEqual(reminder["case"], request)
        self.assertIn("پذیرش راهنمایی دانشجو", reminder["what"])

    def test_students_and_edu_get_no_reminder_card(self):
        self.to_referee_review()
        for user in (self.student, self.edu, self.head):
            self.login(user)
            self.assertEqual(self.client.get(reverse("dashboard")).context["reminders"], [], user.username)

    def test_student_sees_deadlines_as_month_day_timers(self):
        case = self.new_case()
        self.login(self.student)
        response = self.client.get(reverse("dashboard"))
        self.assertTrue(response.context["timers"])
        self.assertContains(response, "timer-card")
        self.assertContains(response, "مانده")
        self.login(self.advisor)
        self.assertEqual(self.client.get(reverse("dashboard")).context["timers"], [])


class ProjectListTests(PortalTestCase):
    def test_only_edu_staff_see_the_list_of_all_projects(self):
        case = self.to_referee_review()
        for user in (self.student, self.advisor, self.ref1, self.head, self.outsider):
            self.login(user)
            self.assertEqual(self.client.get(reverse("project_list")).status_code, 403, user.username)
            self.assertNotContains(self.client.get(reverse("dashboard")), reverse("project_list"))
        self.login(self.edu)
        response = self.client.get(reverse("project_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("case_detail", args=[case.pk]))
        self.assertContains(response, case.title_fa)
        self.assertContains(response, reverse("project_list"))      # پیوند در منو

    def test_search_and_state_filter(self):
        a = self.to_referee_review()
        b = self.choose_advisor(self.new_case(self.external), self.external)
        self.login(self.edu)
        url = reverse("project_list")
        self.assertEqual({c.pk for c in self.client.get(url).context["cases"]}, {a.pk, b.pk})
        self.assertEqual([c.pk for c in self.client.get(url, {"state": State.REFEREE_REVIEW}).context["cases"]], [a.pk])
        self.assertEqual([c.pk for c in self.client.get(url, {"q": "external"}).context["cases"]], [b.pk])


class VersionHistoryPortalTests(PortalTestCase):
    def test_referee_cannot_open_or_download_previous_versions(self):
        case = self.to_referee_review(referees=[self.ref1, self.ref2])
        case = services.perform(case, "submit_review", self.ref1, decision=ReviewDecision.REVISE, note="اصلاح")
        case = services.perform(case, "submit_review", self.ref2, decision=ReviewDecision.APPROVE)
        services.upload_document(case, self.student, DocKind.PROPOSAL, pdf("v2.pdf"))
        case = services.perform(case, "resubmit_proposal", self.student)
        old, new = case.versions.get(number=1), case.versions.get(number=2)
        expected = {self.student: (200, 200), self.advisor: (200, 200), self.edu: (200, 200),
                    self.ref1: (404, 200), self.head: (404, 200)}
        for user, (old_status, new_status) in expected.items():
            self.login(user)
            self.assertEqual(self.client.get(reverse("document_download", args=[old.pk])).status_code,
                             old_status, user.username)
            self.assertEqual(self.client.get(reverse("document_download", args=[new.pk])).status_code,
                             new_status, user.username)

    def test_returned_proposal_shows_the_recorded_comment_to_the_student(self):
        case = self.choose_advisor(self.new_case())
        services.upload_document(case, self.student, DocKind.PROPOSAL, pdf())
        case = services.perform(case, "submit_proposal", self.student)
        services.perform(case, "advisor_return", self.advisor, note="هدف پروژه روشن نیست")
        self.login(self.student)
        response = self.client.get(reverse("case_detail", args=[case.pk]))
        self.assertContains(response, 'class="card feedback"')
        self.assertContains(response, "هدف پروژه روشن نیست")
