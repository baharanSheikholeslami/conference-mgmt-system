"""آزمون‌های پورتال (ورود، داشبورد، فرم‌ها و کنترل دسترسی) با کلاینت آزمون جنگو."""
from django.test import override_settings
from django.urls import reverse

from . import rules, services
from .enums import DocKind, ReviewDecision, State
from .jalali import parse_jalali, to_jalali
from .models import ProjectCase
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
        self.login(self.head)                          # حالا نوبت مدیر گروه است
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(len(response.context["pending"]), 1)
        self.assertContains(response, "تصویب پیشنهاد در گروه")

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
        self.assertEqual((case.state, case.advisor), (State.DRAFTING, self.advisor))

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
        case = services.perform(self.new_case(), "select_advisor", self.student, advisor=self.advisor)
        self.login(self.student)
        response = self.client.post(reverse("document_upload", args=[case.pk]),
                                    {"file": pdf("virus.exe")})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(case.versions.exists())

    def test_required_note_is_enforced_by_the_form(self):
        case = services.perform(self.new_case(), "select_advisor", self.student, advisor=self.advisor)
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

        self.login(self.advisor)
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
