from datetime import date

from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase

from . import rules, statemachine as sm
from .enums import CaseRole, DeadlineKind, Semester, State, UserType
from .models import Department, ProjectCase, RefereeAssignment, User


class StateMachineTests(SimpleTestCase):
    def test_every_state_is_reachable_from_initial(self):
        self.assertEqual(sm.reachable_states(), set(State))

    def test_terminal_states_have_no_outgoing_transitions(self):
        for s in sm.TERMINAL_STATES:
            self.assertEqual(sm.transitions_from(s), [], s)

    def test_active_states_are_never_dead_ends(self):
        for s in sm.ACTIVE_STATES:
            self.assertTrue(sm.transitions_from(s), f"{s} بن‌بست است")

    def test_no_transition_starts_from_a_terminal_state(self):
        for t in sm.TRANSITIONS.values():
            self.assertFalse(set(t.sources) & set(sm.TERMINAL_STATES), t.action)

    def test_action_keys_and_actors_are_valid(self):
        for key, t in sm.TRANSITIONS.items():
            self.assertEqual(key, t.action)
            self.assertIn(t.actor, list(CaseRole))

    def test_rejections_and_returns_require_a_note(self):
        for a in ("advisor_return", "group_reject", "council_reject", "thesis_return", "cancel"):
            self.assertTrue(sm.TRANSITIONS[a].note_required, a)


class RulesTests(SimpleTestCase):
    def test_add_months_clamps_day(self):
        self.assertEqual(rules.add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(rules.add_months(date(2026, 11, 15), 3), date(2027, 2, 15))

    def test_grade_deadline_first_semester_is_30_mehr_next_year(self):
        # ۳۰ مهر ۱۴۰۵ = ۲۲ اکتبر ۲۰۲۶
        self.assertEqual(rules.grade_entry_deadline(1404, Semester.FIRST), date(2026, 10, 22))
        self.assertEqual(rules.grade_entry_deadline(1404, Semester.SUMMER), date(2026, 10, 22))

    def test_grade_deadline_second_semester_is_31_farvardin(self):
        d = rules.grade_entry_deadline(1404, Semester.SECOND)
        self.assertGreater(d, rules.grade_entry_deadline(1404, Semester.FIRST))
        self.assertEqual((d.year, d.month), (2027, 4))

    def test_thesis_deadline_is_three_weeks_earlier(self):
        gd = rules.grade_entry_deadline(1404, Semester.FIRST)
        self.assertEqual((gd - rules.thesis_delivery_deadline(1404, Semester.FIRST)).days, 21)


class ModelTests(TestCase):
    def setUp(self):
        self.dept = Department.objects.create(name="CE")
        mk = lambda name, t, dept=self.dept, **kw: User.objects.create_user(
            name, password="x", user_type=t, department=dept, **kw)
        self.student = mk("s", UserType.STUDENT)
        self.external = mk("x", UserType.EXTERNAL)
        self.advisor = mk("a", UserType.FACULTY)
        self.referee = mk("r", UserType.FACULTY)
        self.edu = mk("e", UserType.EDU_STAFF, dept=None)

    def make_case(self, author, **kw):
        return ProjectCase.objects.create(
            author=author, advisor=self.advisor, department=self.dept, title_fa="آزمون",
            academic_year=1404, semester=Semester.FIRST, registration_date=date(2025, 10, 1),
            state=State.DRAFTING, **kw)

    def test_student_case_is_graded_and_needs_one_referee(self):
        c = self.make_case(self.student)
        self.assertTrue(c.is_graded)
        self.assertEqual(c.required_referees, 1)

    def test_non_student_case_needs_two_referees_and_has_no_grade_deadlines(self):
        c = self.make_case(self.external)
        self.assertFalse(c.is_graded)
        self.assertEqual(c.required_referees, 2)
        kinds = set(c.expected_deadlines())
        self.assertNotIn(DeadlineKind.GRADE_ENTRY, kinds)
        self.assertNotIn(DeadlineKind.THESIS_DELIVERY, kinds)

    def test_sync_deadlines(self):
        c = self.make_case(self.student)
        c.sync_deadlines()
        due = {d.kind: d.due_date for d in c.deadlines.all()}
        self.assertEqual(due[DeadlineKind.PROPOSAL_SUBMISSION], date(2025, 12, 1))
        self.assertEqual(due[DeadlineKind.GRADE_ENTRY], date(2026, 10, 22))

    def test_roles_are_contextual(self):
        c = self.make_case(self.student)
        RefereeAssignment.objects.create(case=c, referee=self.referee, assigned_by=self.advisor)
        self.assertEqual(c.roles_of(self.student), {CaseRole.AUTHOR})
        self.assertEqual(c.roles_of(self.advisor), {CaseRole.ADVISOR})
        self.assertEqual(c.roles_of(self.referee), {CaseRole.REFEREE})
        self.assertEqual(c.roles_of(self.edu), {CaseRole.EDU})

    def test_only_one_head_per_department(self):
        User.objects.filter(pk=self.advisor.pk).update(is_department_head=True)
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.filter(pk=self.referee.pk).update(is_department_head=True)

    def test_advisor_required_after_selection_state(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProjectCase.objects.create(
                author=self.student, title_fa="x", academic_year=1404, semester=Semester.FIRST,
                registration_date=date(2025, 10, 1), state=State.DRAFTING)

    def test_one_active_case_per_author_per_term(self):
        self.make_case(self.student)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_case(self.student)
