"""آزمون‌های موتور گردش‌کار (services.py) — بدون مرورگر، مستقیم روی توابع."""
import shutil
import tempfile
from datetime import date, timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from . import rules, services
from .enums import CaseRole, DeadlineKind, DocKind, ReviewDecision, Semester, State, UserType
from .models import CaseEvent, Department, ProjectCase, User

MEDIA = tempfile.mkdtemp(prefix="test-media-")


def pdf(name="proposal.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4 test", content_type="application/pdf")


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

    def new_case(self, author=None):
        return services.create_case(author or self.student, "عنوان آزمایشی")

    def to_referee_review(self, author=None, referees=None):
        """پرونده را تا وضعیت «در حال داوری» جلو می‌برد."""
        author = author or self.student
        case = self.new_case(author)
        case = self.do(case, "select_advisor", author, advisor=self.advisor)
        services.upload_document(case, author, DocKind.PROPOSAL, pdf())
        case = self.do(case, "submit_proposal", author)
        case = self.do(case, "advisor_approve", self.advisor)
        case = self.do(case, "edu_register", self.edu)
        return self.do(case, "assign_referees", self.head, referees=referees or [self.ref1])

    def to_in_progress(self):
        case = self.to_referee_review()
        case = self.do(case, "submit_review", self.ref1, decision=ReviewDecision.APPROVE)
        case = self.do(case, "group_approve", self.head)
        return self.do(case, "council_approve", self.edu)


class HappyPathTests(WorkflowTestCase):
    def test_full_lifecycle_from_advisor_selection_to_grade(self):
        case = self.new_case()
        self.assertEqual(case.state, State.ADVISOR_SELECTION)

        case = self.do(case, "select_advisor", self.student, advisor=self.advisor)
        self.assertEqual((case.state, case.advisor, case.department),
                         (State.DRAFTING, self.advisor, self.dept))

        services.upload_document(case, self.student, DocKind.PROPOSAL, pdf())
        case = self.do(case, "submit_proposal", self.student)
        case = self.do(case, "advisor_approve", self.advisor)
        case = self.do(case, "edu_register", self.edu)
        self.assertEqual(case.edu_received_at, timezone.localdate())

        case = self.do(case, "assign_referees", self.head, referees=[self.ref1])
        case = self.do(case, "submit_review", self.ref1, decision=ReviewDecision.APPROVE)
        self.assertEqual(case.state, State.GROUP_APPROVAL)

        case = self.do(case, "group_approve", self.head)
        case = self.do(case, "council_approve", self.edu)
        self.assertEqual((case.state, case.final_approved_at),
                         (State.IN_PROGRESS, timezone.localdate()))

        services.upload_document(case, self.student, DocKind.THESIS, pdf("thesis.pdf"))
        case = self.do(case, "submit_thesis", self.student)
        case = self.do(case, "thesis_approve", self.advisor)
        defense = rules.add_months(case.final_approved_at, 3)
        case = self.do(case, "schedule_defense", self.edu, defense_date=defense,
                       defense_place="کلاس ۱۰۱")
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
                         ["create_case", "select_advisor", "upload_proposal", "submit_proposal",
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
            self.do(case, "select_advisor", self.student, advisor=self.edu)

    def test_proposal_file_is_required_before_submission(self):
        case = self.do(self.new_case(), "select_advisor", self.student, advisor=self.advisor)
        offer = services.available_actions(case, self.student)[0]
        self.assertEqual(offer.transition.action, "submit_proposal")
        self.assertTrue(offer.blocked)
        with self.assertRaises(services.GuardFailed):
            self.do(case, "submit_proposal", self.student)

    def test_returning_needs_a_note(self):
        case = self.do(self.new_case(), "select_advisor", self.student, advisor=self.advisor)
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
        case = self.do(case, "select_advisor", self.external, advisor=self.advisor)
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
        self.assertEqual(case.state, State.GROUP_APPROVAL)

        case = self.do(case, "group_approve", self.head)
        case = self.do(case, "council_approve", self.edu)
        services.upload_document(case, self.external, DocKind.THESIS, pdf())
        case = self.do(case, "submit_thesis", self.external)
        case = self.do(case, "thesis_approve", self.advisor)
        case = self.do(case, "schedule_defense", self.edu, defense_place="سالن",
                       defense_date=rules.add_months(case.final_approved_at, 4))
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
        self.assertEqual(case.state, State.GROUP_APPROVAL)

    def test_upload_is_limited_to_author_and_state(self):
        case = self.to_referee_review()
        with self.assertRaises(services.NotAllowed):                # در حال داوری
            services.upload_document(case, self.student, DocKind.PROPOSAL, pdf())
        case2 = self.do(self.new_case(self.external), "select_advisor", self.external,
                        advisor=self.advisor)
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
        with self.assertRaises(services.GuardFailed):
            self.do(case, "record_result", self.advisor, grade="21")
        with self.assertRaises(services.GuardFailed):
            self.do(case, "record_result", self.advisor)

    def test_rejection_and_cancellation_are_terminal(self):
        case = self.to_referee_review()
        case = self.do(case, "submit_review", self.ref1, decision=ReviewDecision.APPROVE)
        case = self.do(case, "group_reject", self.head, note="موضوع تکراری است")
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
        self.assertEqual(d[DeadlineKind.GROUP_REVIEW].satisfied_at, today)
        self.assertEqual(d[DeadlineKind.DEFENSE_EARLIEST].due_date, rules.add_months(today, 3))
        self.assertFalse(case.events.get(action="edu_register").data["late"])

    def to_in_progress_from(self, case):
        case = self.do(case, "select_advisor", self.student, advisor=self.advisor)
        services.upload_document(case, self.student, DocKind.PROPOSAL, pdf())
        case = self.do(case, "submit_proposal", self.student)
        case = self.do(case, "advisor_approve", self.advisor)
        case = self.do(case, "edu_register", self.edu)
        case = self.do(case, "assign_referees", self.head, referees=[self.ref1])
        case = self.do(case, "submit_review", self.ref1, decision=ReviewDecision.APPROVE)
        case = self.do(case, "group_approve", self.head)
        return self.do(case, "council_approve", self.edu)

    def test_late_registration_is_flagged_but_not_blocked(self):
        case = self.do(self.new_case(), "select_advisor", self.student, advisor=self.advisor)
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
