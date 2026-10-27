"""آزمون‌های موتور گردش‌کار (services.py) — بدون مرورگر، مستقیم روی توابع."""
import shutil
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from . import rules, services
from .enums import CaseRole, DeadlineKind, DocKind, ReviewDecision, Semester, State, UserType
from .models import CaseEvent, Department, ProjectCase, User

MEDIA = tempfile.mkdtemp(prefix="test-media-")


def pdf(name="proposal.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4 test", content_type="application/pdf")


def signature_png(name="signature.png"):
    """تصویر امضای کوچک و معتبر برای آزمون تأیید سند."""
    import io
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGBA", (60, 24), (20, 30, 90, 255)).save(out, "PNG")
    return SimpleUploadedFile(name, out.getvalue(), content_type="image/png")


@override_settings(MEDIA_ROOT=MEDIA)
class WorkflowTestCase(TestCase):
    """پایه‌ی مشترک: یک گروه، همه‌ی نقش‌ها و چند تابع میان‌بر."""

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    @classmethod
    def setUpTestData(cls):
        cls.dept = Department.objects.create(name="مهندسی کامپیوتر")
        cls.other_dept = Department.objects.create(name="ریاضی")

        def mk(name, utype, dept=None, **kw):
            return User.objects.create_user(name, password="pass12345", user_type=utype,
                                            department=dept, **kw)
        cls.student = mk("student", UserType.STUDENT)
        cls.external = mk("external", UserType.EXTERNAL)
        cls.advisor = mk("advisor", UserType.FACULTY, cls.dept)
        cls.ref1 = mk("ref1", UserType.FACULTY, cls.dept)
        cls.ref2 = mk("ref2", UserType.FACULTY, cls.dept)
        cls.head = mk("head", UserType.FACULTY, cls.dept, is_department_head=True)
        cls.outsider = mk("outsider", UserType.FACULTY, cls.other_dept)
        cls.edu = mk("edu", UserType.EDU_STAFF)

    def do(self, case, action, actor, **kw):
        return services.perform(case, action, actor, **kw)

    def on(self, day):
        """«سفر در زمان»: درون بلوک with، تاریخ امروز برای کل سامانه برابر day است."""
        return mock.patch("django.utils.timezone.localdate", return_value=day)

    def new_case(self, author=None):
        return services.create_case(author or self.student, "عنوان آزمایشی")

    def choose_advisor(self, case, author=None, advisor=None):
        """دانشجو استاد را انتخاب می‌کند و استاد درخواست را می‌پذیرد؛ پرونده به «تدوین پیشنهاد» می‌رود."""
        advisor = advisor or self.advisor
        case = self.do(case, "select_advisor", author or self.student, advisor=advisor)
        return self.do(case, "advisor_accept", advisor)

    def to_referee_review(self, author=None, referees=None):
        """پرونده را تا وضعیت «در حال داوری» جلو می‌برد."""
        author = author or self.student
        case = self.new_case(author)
        case = self.choose_advisor(case, author, self.advisor)
        services.upload_document(case, author, DocKind.PROPOSAL, pdf())
        case = self.do(case, "submit_proposal", author)
        case = self.do(case, "advisor_approve", self.advisor)
        case = self.do(case, "edu_register", self.edu)
        return self.do(case, "assign_referees", self.head, referees=referees or [self.ref1])

    def to_in_progress(self):
        case = self.to_referee_review()
        case = self.do(case, "submit_review", self.ref1, decision=ReviewDecision.APPROVE)
        return self.do(case, "council_approve", self.edu)


class HappyPathTests(WorkflowTestCase):
    def test_full_lifecycle_from_advisor_selection_to_grade(self):
        case = self.new_case()
        self.assertEqual(case.state, State.ADVISOR_SELECTION)

        case = self.do(case, "select_advisor", self.student, advisor=self.advisor)
        self.assertEqual((case.state, case.advisor, case.department),
                         (State.ADVISOR_PENDING, self.advisor, self.dept))
        case = self.do(case, "advisor_accept", self.advisor)
        self.assertEqual(case.state, State.DRAFTING)

        services.upload_document(case, self.student, DocKind.PROPOSAL, pdf())
        case = self.do(case, "submit_proposal", self.student)
        case = self.do(case, "advisor_approve", self.advisor)
        case = self.do(case, "edu_register", self.edu)
        self.assertEqual(case.edu_received_at, timezone.localdate())

        case = self.do(case, "assign_referees", self.head, referees=[self.ref1])
        case = self.do(case, "submit_review", self.ref1, decision=ReviewDecision.APPROVE)
        self.assertEqual(case.state, State.COUNCIL_APPROVAL)
        case = self.do(case, "council_approve", self.edu)
        self.assertEqual((case.state, case.final_approved_at),
                         (State.IN_PROGRESS, timezone.localdate()))

        services.upload_document(case, self.student, DocKind.THESIS, pdf("thesis.pdf"))
        case = self.do(case, "submit_thesis", self.student)
        case = self.do(case, "thesis_approve", self.advisor)
        defense = rules.add_months(case.final_approved_at, 3)
        case = self.do(case, "schedule_defense", self.edu, defense_date=defense,
                       defense_place="کلاس ۱۰۱")
        with self.on(defense):                                       # روز دفاع
            case = self.do(case, "record_result", self.advisor, grade="18.5")

        self.assertEqual((case.state, case.grade), (State.COMPLETED, Decimal("18.5")))
        self.assertTrue(case.is_terminal)
        self.assertEqual(services.available_actions(case, self.edu), [])

    def test_every_step_is_logged_with_actor_and_states(self):
        case = self.to_referee_review()
        events = list(case.events.values_list("action", "from_state", "to_state"))
        self.assertEqual(events[0], ("create_case", State.ADVISOR_SELECTION, State.ADVISOR_SELECTION))
        self.assertEqual(events[-1], ("assign_referees", State.REFEREE_ASSIGNMENT, State.REFEREE_REVIEW))
        self.assertEqual([a for a, _, _ in events],
                         ["create_case", "select_advisor", "advisor_accept", "upload_proposal", "submit_proposal",
                          "advisor_approve", "edu_register", "assign_referees"])
        self.assertEqual(case.events.get(action="edu_register").actor, self.edu)


class PermissionTests(WorkflowTestCase):
    def test_wrong_role_cannot_act(self):
        case = self.to_referee_review()
        for user in (self.student, self.advisor, self.head, self.edu, self.ref2):
            with self.assertRaises(services.NotAllowed, msg=user.username):
                self.do(case, "submit_review", user, decision=ReviewDecision.APPROVE)

    def test_action_is_rejected_in_the_wrong_state(self):
        case = self.new_case()
        with self.assertRaises(services.NotAllowed):
            self.do(case, "submit_proposal", self.student)

    def test_failed_action_changes_nothing(self):
        case = self.to_referee_review()
        before = CaseEvent.objects.count()
        with self.assertRaises(services.WorkflowError):
            self.do(case, "submit_review", self.ref1, decision=ReviewDecision.REVISE)  # بدون نظر
        case.refresh_from_db()
        self.assertEqual(case.state, State.REFEREE_REVIEW)
        self.assertEqual(CaseEvent.objects.count(), before)

    def test_only_authors_can_create_cases(self):
        with self.assertRaises(services.NotAllowed):
            services.create_case(self.advisor, "x")

    def test_second_active_case_in_the_same_term_is_refused(self):
        self.new_case()
        with self.assertRaises(services.GuardFailed):
            self.new_case()

    def test_visibility_follows_roles(self):
        case = self.to_referee_review()
        visible = lambda u: services.cases_for(u).filter(pk=case.pk).exists()
        for user in (self.student, self.advisor, self.ref1, self.head, self.edu):
            self.assertTrue(visible(user), user.username)
        for user in (self.external, self.ref2, self.outsider):
            self.assertFalse(visible(user), user.username)


class GuardTests(WorkflowTestCase):
    def test_advisor_must_be_faculty_with_department(self):
        case = self.new_case()
        with self.assertRaises(services.GuardFailed):
            self.choose_advisor(case, self.student, self.edu)

    def test_proposal_file_is_required_before_submission(self):
        case = self.choose_advisor(self.new_case(), self.student, self.advisor)
        offer = services.available_actions(case, self.student)[0]
        self.assertEqual(offer.transition.action, "submit_proposal")
        self.assertTrue(offer.blocked)
        with self.assertRaises(services.GuardFailed):
            self.do(case, "submit_proposal", self.student)

    def test_returning_needs_a_note(self):
        case = self.choose_advisor(self.new_case(), self.student, self.advisor)
        services.upload_document(case, self.student, DocKind.PROPOSAL, pdf())
        case = self.do(case, "submit_proposal", self.student)
        with self.assertRaises(services.GuardFailed):
            self.do(case, "advisor_return", self.advisor, note="   ")
        case = self.do(case, "advisor_return", self.advisor, note="بخش ۳ ناقص است")
        self.assertEqual(case.state, State.DRAFTING)

    def test_referee_must_be_eligible(self):
        case = self.to_referee_review()
        case.state = State.REFEREE_ASSIGNMENT
        case.save()
        for bad in (self.advisor, self.outsider, self.student):
            with self.assertRaises(services.GuardFailed, msg=bad.username):
                self.do(case, "assign_referees", self.head, referees=[bad])

    def test_non_student_author_needs_two_referees_and_gets_no_grade(self):
        case = self.new_case(self.external)
        case = self.choose_advisor(case, self.external, self.advisor)
        services.upload_document(case, self.external, DocKind.PROPOSAL, pdf())
        case = self.do(case, "submit_proposal", self.external)
        case = self.do(case, "advisor_approve", self.advisor)
        case = self.do(case, "edu_register", self.edu)
        with self.assertRaises(services.GuardFailed):
            self.do(case, "assign_referees", self.head, referees=[self.ref1])
        case = self.do(case, "assign_referees", self.head, referees=[self.ref1, self.ref2])

        case = self.do(case, "submit_review", self.ref1, decision=ReviewDecision.APPROVE)
        self.assertEqual(case.state, State.REFEREE_REVIEW)          # هنوز داور دوم رأی نداده
        self.assertFalse(services.is_pending_for(case, self.ref1))
        self.assertTrue(services.is_pending_for(case, self.ref2))
        case = self.do(case, "submit_review", self.ref2, decision=ReviewDecision.APPROVE)
        self.assertEqual(case.state, State.COUNCIL_APPROVAL)
        case = self.do(case, "council_approve", self.edu)
        services.upload_document(case, self.external, DocKind.THESIS, pdf())
        case = self.do(case, "submit_thesis", self.external)
        case = self.do(case, "thesis_approve", self.advisor)
        defense = rules.add_months(case.final_approved_at, 4)
        case = self.do(case, "schedule_defense", self.edu, defense_place="سالن", defense_date=defense)
        with self.on(defense + timedelta(days=1)):
            with self.assertRaises(services.GuardFailed):
                self.do(case, "record_result", self.advisor, grade="19")
            case = self.do(case, "record_result", self.advisor)
        self.assertEqual((case.state, case.grade), (State.COMPLETED, None))

    def test_revision_loop(self):
        case = self.to_referee_review(referees=[self.ref1, self.ref2])
        case = self.do(case, "submit_review", self.ref1, decision=ReviewDecision.APPROVE)
        case = self.do(case, "submit_review", self.ref2, decision=ReviewDecision.REVISE,
                       note="روش ارزیابی مشخص نیست")
        self.assertEqual(case.state, State.REVISION)

        with self.assertRaises(services.GuardFailed):               # بدون نسخه‌ی جدید
            self.do(case, "resubmit_proposal", self.student)
        v2 = services.upload_document(case, self.student, DocKind.PROPOSAL, pdf("v2.pdf"))
        self.assertEqual(v2.number, 2)
        case = self.do(case, "resubmit_proposal", self.student)
        self.assertEqual(case.state, State.REFEREE_REVIEW)

        # دور دوم: هر دو داور باید دوباره برای نسخه‌ی ۲ رأی بدهند
        case = self.do(case, "submit_review", self.ref1, decision=ReviewDecision.APPROVE)
        with self.assertRaises(services.GuardFailed):               # رأی تکراری
            self.do(case, "submit_review", self.ref1, decision=ReviewDecision.APPROVE)
        case = self.do(case, "submit_review", self.ref2, decision=ReviewDecision.APPROVE)
        self.assertEqual(case.state, State.COUNCIL_APPROVAL)

    def test_upload_is_limited_to_author_and_state(self):
        case = self.to_referee_review()
        with self.assertRaises(services.NotAllowed):                # در حال داوری
            services.upload_document(case, self.student, DocKind.PROPOSAL, pdf())
        case2 = self.choose_advisor(self.new_case(self.external), self.external, self.advisor)
        with self.assertRaises(services.NotAllowed):                # نویسنده‌ی پرونده نیست
            services.upload_document(case2, self.advisor, DocKind.PROPOSAL, pdf())

    def test_defense_must_be_three_months_after_final_approval(self):
        case = self.to_in_progress()
        services.upload_document(case, self.student, DocKind.THESIS, pdf())
        case = self.do(case, "submit_thesis", self.student)
        case = self.do(case, "thesis_approve", self.advisor)
        earliest = rules.add_months(case.final_approved_at, 3)
        with self.assertRaises(services.GuardFailed):
            self.do(case, "schedule_defense", self.edu, defense_place="سالن",
                    defense_date=earliest - timedelta(days=1))
        with self.assertRaises(services.GuardFailed):               # بدون محل
            self.do(case, "schedule_defense", self.edu, defense_date=earliest)
        case = self.do(case, "schedule_defense", self.edu, defense_place="سالن",
                       defense_date=earliest)
        with self.on(earliest):
            with self.assertRaises(services.GuardFailed):
                self.do(case, "record_result", self.advisor, grade="21")
            with self.assertRaises(services.GuardFailed):
                self.do(case, "record_result", self.advisor)

    def test_result_cannot_be_recorded_before_the_defense_day(self):
        case = self.to_in_progress()
        services.upload_document(case, self.student, DocKind.THESIS, pdf())
        case = self.do(case, "submit_thesis", self.student)
        case = self.do(case, "thesis_approve", self.advisor)
        defense = rules.add_months(case.final_approved_at, 3)
        case = self.do(case, "schedule_defense", self.edu, defense_place="سالن", defense_date=defense)

        with self.on(defense - timedelta(days=1)):
            offer = services.available_actions(case, self.advisor)[0]
            self.assertTrue(offer.blocked and offer.waiting)
            self.assertFalse(services.is_pending_for(case, self.advisor))   # هنوز کاری از او ساخته نیست
            with self.assertRaises(services.GuardFailed):
                self.do(case, "record_result", self.advisor, grade="18")
        with self.on(defense):
            self.assertTrue(services.is_pending_for(case, self.advisor))
            case = self.do(case, "record_result", self.advisor, grade="18")
        self.assertEqual(case.state, State.COMPLETED)

    def test_rejection_and_cancellation_are_terminal(self):
        case = self.to_referee_review()
        case = self.do(case, "submit_review", self.ref1, decision=ReviewDecision.APPROVE)
        self.assertEqual(case.state, State.COUNCIL_APPROVAL)
        case = self.do(case, "council_reject", self.edu, note="موضوع تکراری است")
        self.assertEqual(case.state, State.REJECTED)
        self.assertEqual(services.available_actions(case, self.edu), [])
        # پس از رد، دانشجو می‌تواند در همان نیم‌سال پرونده‌ی تازه بسازد
        fresh = self.new_case()
        fresh = self.do(fresh, "cancel", self.edu, note="انصراف دانشجو")
        self.assertEqual(fresh.state, State.CANCELLED)


class DeadlineTests(WorkflowTestCase):
    def test_deadlines_are_created_and_satisfied_along_the_way(self):
        case = self.new_case()
        kinds = lambda: {d.kind: d for d in case.deadlines.all()}
        self.assertEqual(set(kinds()), {DeadlineKind.PROPOSAL_SUBMISSION,
                                        DeadlineKind.THESIS_DELIVERY, DeadlineKind.GRADE_ENTRY})
        self.assertEqual(services.next_deadline(case).kind, DeadlineKind.PROPOSAL_SUBMISSION)

        case = self.to_in_progress_from(case)
        d = kinds()
        today = timezone.localdate()
        self.assertEqual(d[DeadlineKind.PROPOSAL_SUBMISSION].satisfied_at, today)
        self.assertEqual(d[DeadlineKind.GROUP_REVIEW].satisfied_at, today)   # با پایان داوری
        self.assertEqual(d[DeadlineKind.DEFENSE_EARLIEST].due_date, rules.add_months(today, 3))
        self.assertFalse(case.events.get(action="edu_register").data["late"])

    def to_in_progress_from(self, case):
        case = self.choose_advisor(case, self.student, self.advisor)
        services.upload_document(case, self.student, DocKind.PROPOSAL, pdf())
        case = self.do(case, "submit_proposal", self.student)
        case = self.do(case, "advisor_approve", self.advisor)
        case = self.do(case, "edu_register", self.edu)
        case = self.do(case, "assign_referees", self.head, referees=[self.ref1])
        case = self.do(case, "submit_review", self.ref1, decision=ReviewDecision.APPROVE)
        return self.do(case, "council_approve", self.edu)

    def test_late_registration_is_flagged_but_not_blocked(self):
        case = self.choose_advisor(self.new_case(), self.student, self.advisor)
        services.upload_document(case, self.student, DocKind.PROPOSAL, pdf())
        case = self.do(case, "submit_proposal", self.student)
        case = self.do(case, "advisor_approve", self.advisor)
        # ثبت‌نام را به سه ماه پیش می‌بریم تا مهلت دوماهه گذشته باشد
        ProjectCase.objects.filter(pk=case.pk).update(
            registration_date=timezone.localdate() - timedelta(days=95))
        case.refresh_from_db()
        case.sync_deadlines()
        self.assertTrue(case.deadlines.get(kind=DeadlineKind.PROPOSAL_SUBMISSION).is_overdue)

        case = self.do(case, "edu_register", self.edu)
        self.assertEqual(case.state, State.REFEREE_ASSIGNMENT)
        self.assertTrue(case.events.get(action="edu_register").data["late"])


class TermRuleTests(WorkflowTestCase):
    def test_current_term(self):
        self.assertEqual(rules.current_term(date(2026, 10, 4)), (1405, Semester.FIRST))   # ۱۲ مهر ۱۴۰۵
        self.assertEqual(rules.current_term(date(2027, 2, 10)), (1405, Semester.SECOND))  # بهمن ۱۴۰۵
        self.assertEqual(rules.current_term(date(2027, 5, 1)), (1405, Semester.SECOND))   # اردیبهشت ۱۴۰۶
        self.assertEqual(rules.current_term(date(2027, 8, 1)), (1405, Semester.SUMMER))   # مرداد ۱۴۰۶

    def test_roles_shown_to_dashboard(self):
        case = self.to_referee_review()
        self.assertEqual(case.roles_of(self.ref1), {CaseRole.REFEREE})
        self.assertTrue(services.is_pending_for(case, self.ref1))
        self.assertFalse(services.is_pending_for(case, self.edu))    # فقط «لغو» دارد


class AdvisorRequestTests(WorkflowTestCase):
    """درخواست راهنمایی: استاد باید بپذیرد و هر استاد حداکثر دو دانشجو دارد."""

    def other_students(self, n):
        return [User.objects.create_user(f"s{i}", password="x", user_type=UserType.STUDENT)
                for i in range(n)]

    def test_request_waits_for_the_advisor_and_only_the_advisor_can_answer(self):
        case = self.do(self.new_case(), "select_advisor", self.student, advisor=self.advisor)
        self.assertEqual(case.state, State.ADVISOR_PENDING)
        self.assertTrue(services.is_pending_for(case, self.advisor))
        self.assertFalse(services.is_pending_for(case, self.student))
        for user in (self.student, self.head, self.edu, self.ref1):
            with self.assertRaises(services.NotAllowed, msg=user.username):
                self.do(case, "advisor_accept", user)
        # تا پذیرش، نه آپلود ممکن است و نه ارسال پیشنهاد
        with self.assertRaises(services.NotAllowed):
            services.upload_document(case, self.student, DocKind.PROPOSAL, pdf())

    def test_decline_needs_a_note_and_sends_the_student_back_to_choose_again(self):
        case = self.do(self.new_case(), "select_advisor", self.student, advisor=self.advisor)
        with self.assertRaises(services.GuardFailed):
            self.do(case, "advisor_decline", self.advisor, note="  ")
        case = self.do(case, "advisor_decline", self.advisor, note="ظرفیت تخصصی ندارم")
        self.assertEqual((case.state, case.advisor, case.department),
                         (State.ADVISOR_SELECTION, None, None))
        feedback = services.pending_feedback(case)
        self.assertEqual(feedback["items"][0]["note"], "ظرفیت تخصصی ندارم")
        # دانشجو می‌تواند استاد دیگری را انتخاب کند
        other = User.objects.create_user("adv2", password="x", user_type=UserType.FACULTY,
                                         department=self.dept)
        case = self.do(case, "select_advisor", self.student, advisor=other)
        self.assertEqual(case.state, State.ADVISOR_PENDING)

    def test_an_advisor_has_at_most_two_students(self):
        a, b, c = self.other_students(3)
        self.choose_advisor(self.new_case(a), a)
        self.choose_advisor(self.new_case(b), b)
        self.assertEqual(services.advisee_count(self.advisor), 2)
        self.assertNotIn(self.advisor, services.eligible_advisors())
        case = self.new_case(c)
        with self.assertRaises(services.GuardFailed):              # درخواست به استاد پُر
            self.do(case, "select_advisor", c, advisor=self.advisor)

    def test_pending_requests_do_not_use_up_capacity_but_acceptance_rechecks_it(self):
        a, b, c = self.other_students(3)
        pending = [self.do(self.new_case(u), "select_advisor", u, advisor=self.advisor)
                   for u in (a, b, c)]                              # هر سه درخواست می‌توانند ثبت شوند
        self.assertTrue(all(p.state == State.ADVISOR_PENDING for p in pending))
        self.do(pending[0], "advisor_accept", self.advisor)
        self.do(pending[1], "advisor_accept", self.advisor)
        offer = services.available_actions(pending[2], self.advisor)[0]
        self.assertIn("پر است", offer.blocked)
        with self.assertRaises(services.GuardFailed):
            self.do(pending[2], "advisor_accept", self.advisor)
        case = self.do(pending[2], "advisor_decline", self.advisor, note="ظرفیتم پر شد")
        self.assertEqual(case.state, State.ADVISOR_SELECTION)

    def test_finished_projects_free_a_slot(self):
        a, b = self.other_students(2)
        case_a = self.choose_advisor(self.new_case(a), a)
        self.choose_advisor(self.new_case(b), b)
        self.assertFalse(services.advisor_has_room(self.advisor))
        self.do(case_a, "cancel", self.edu, note="انصراف")
        self.assertTrue(services.advisor_has_room(self.advisor))


class FeedbackAndVisibilityTests(WorkflowTestCase):
    def test_student_sees_the_advisor_return_comment_until_resubmitting(self):
        case = self.choose_advisor(self.new_case())
        self.assertIsNone(services.pending_feedback(case))
        services.upload_document(case, self.student, DocKind.PROPOSAL, pdf())
        case = self.do(case, "submit_proposal", self.student)
        case = self.do(case, "advisor_return", self.advisor, note="بخش ۳ ناقص است")
        feedback = services.pending_feedback(case)
        self.assertEqual([i["note"] for i in feedback["items"]], ["بخش ۳ ناقص است"])
        self.assertEqual(feedback["items"][0]["who"], str(self.advisor))
        case = self.do(case, "submit_proposal", self.student)
        self.assertIsNone(services.pending_feedback(case))

    def test_student_sees_every_referee_comment_after_revision(self):
        case = self.to_referee_review(referees=[self.ref1, self.ref2])
        case = self.do(case, "submit_review", self.ref1, decision=ReviewDecision.REVISE, note="روش مبهم است")
        case = self.do(case, "submit_review", self.ref2, decision=ReviewDecision.APPROVE)
        self.assertEqual(case.state, State.REVISION)
        notes = {i["who"]: i["note"] for i in services.pending_feedback(case)["items"]}
        self.assertEqual(notes, {str(self.ref1): "روش مبهم است"})

    def test_only_author_advisor_and_edu_see_previous_versions(self):
        case = self.to_referee_review(referees=[self.ref1, self.ref2])
        case = self.do(case, "submit_review", self.ref1, decision=ReviewDecision.REVISE, note="اصلاح")
        case = self.do(case, "submit_review", self.ref2, decision=ReviewDecision.APPROVE)
        services.upload_document(case, self.student, DocKind.PROPOSAL, pdf("v2.pdf"))
        case = self.do(case, "resubmit_proposal", self.student)
        numbers = lambda u: [v.number for v in services.visible_versions(case, u)]
        for user in (self.student, self.advisor, self.edu):
            self.assertEqual(numbers(user), [2, 1], user.username)
        for user in (self.ref1, self.ref2, self.head):
            self.assertEqual(numbers(user), [2], user.username)
            old = case.versions.get(number=1)
            self.assertFalse(services.can_download(case, user, old))
            self.assertTrue(services.can_download(case, user, case.versions.get(number=2)))
        uploads = lambda u: [e.data.get("version") for e in services.visible_events(case, u)
                             if e.action == "upload_proposal"]
        self.assertEqual(uploads(self.student), [1, 2])
        self.assertEqual(uploads(self.ref1), [2])


class CountdownTests(TestCase):
    def test_countdown_in_months_and_days(self):
        today = date(2026, 10, 21)
        due = rules.add_months(today, 2) + timedelta(days=5)
        self.assertEqual(rules.countdown(due, today), (2, 5, False))
        self.assertEqual(rules.countdown(today, today), (0, 0, False))
        self.assertEqual(rules.countdown(today - timedelta(days=1), today), (0, 1, True))
        self.assertEqual(rules.countdown(today + timedelta(days=11), today), (0, 11, False))
