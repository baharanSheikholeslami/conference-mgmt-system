"""
آزمون‌های فاز ۴. مهم‌ترین آن‌ها test_every_scenario_passes است: همه‌ی سناریوهای سرتاسری
(از انتخاب استاد راهنما تا ثبت نمره) در هر بار `manage.py test` آفلاین اجرا می‌شوند.
"""
import json
from dataclasses import replace

from django.test import TestCase, override_settings

from agents import checklist, extract
from workflow import services as wf
from workflow.enums import DeadlineKind, State
from workflow.models import ProjectCase, User
from workflow.test_services import MEDIA

from . import documents, report
from .runner import PREFIX, Runner, cleanup, scenario_cases
from .scenarios import BY_KEY, GUARD, SCENARIOS, Step


@override_settings(MEDIA_ROOT=MEDIA)
class ScenarioTests(TestCase):
    def run_scenarios(self, scenarios=None, **kw):
        return Runner(persist=True, **kw).run(scenarios)

    def test_every_scenario_passes(self):
        for result in self.run_scenarios():
            with self.subTest(result.scenario.key):
                failed = [s for s in result.steps if not s.ok]
                self.assertFalse(result.error, result.error)
                self.assertEqual(failed, [])
                self.assertEqual(len(result.steps), len(result.scenario.steps))

    def test_scenarios_cover_the_whole_state_machine(self):
        """هر وضعیت ماشین‌حالت دست‌کم در یک سناریو دیده می‌شود."""
        seen = {step.expect for s in SCENARIOS for step in s.steps if isinstance(step.expect, State)}
        self.assertEqual(seen, set(State))

    def test_a_wrong_expectation_is_reported_and_stops_the_scenario(self):
        scenario = BY_KEY["cancel"]
        broken = replace(scenario, steps=(scenario.steps[0], replace(scenario.steps[1], expect=State.REVISION),
                                          *scenario.steps[2:]))
        (result,) = self.run_scenarios([broken])
        self.assertFalse(result.ok)
        self.assertEqual(len(result.steps), 2)               # پس از گام نادرست ادامه نمی‌دهد
        self.assertEqual(result.steps[1].got, State.ADVISOR_PENDING.label)

    def test_a_rule_violation_that_succeeds_is_a_failure(self):
        scenario = BY_KEY["cancel"]
        broken = replace(scenario, steps=(*scenario.steps[:2],
                                          Step(1, "@edu", "cancel", {"note": "x"}, GUARD)))
        (result,) = self.run_scenarios([broken])
        self.assertFalse(result.ok)
        self.assertEqual(result.steps[-1].got, State.CANCELLED.label)

    def test_reminder_mismatch_is_reported(self):
        scenario = BY_KEY["late_and_reminders"]
        wrong = replace(scenario.steps[4], payload={"expect": {DeadlineKind.PROPOSAL_SUBMISSION: ("d7", ["@author"])}})
        (result,) = self.run_scenarios([replace(scenario, steps=(*scenario.steps[:4], wrong))])
        self.assertFalse(result.steps[-1].ok)
        self.assertIn("d14", result.steps[-1].detail)

    def test_offline_run_leaves_no_data_behind(self):
        before = ProjectCase.objects.count()
        results = Runner().run([BY_KEY["cancel"]])
        self.assertTrue(results[0].ok)
        self.assertEqual(ProjectCase.objects.count(), before)

    def test_cleanup_only_touches_scenario_cases(self):
        self.run_scenarios([BY_KEY["cancel"], BY_KEY["council_reject"]])
        other = User.objects.create_user("real_student")
        ProjectCase.objects.create(author=other, title_fa="پرونده‌ی واقعی", academic_year=1405,
                                   semester="first", registration_date="2026-10-01")
        self.assertEqual(scenario_cases().count(), 3)
        self.assertEqual(cleanup(), 3)
        self.assertEqual(ProjectCase.objects.count(), 1)
        scenario_users = User.objects.filter(username__startswith=PREFIX)
        self.assertTrue(scenario_users.exists())                      # کاربران برای اجرای بعد می‌مانند…
        self.assertFalse(scenario_users.filter(is_active=True).exists())  # …اما غیرفعال
        self.assertFalse(wf.eligible_advisors().filter(username__startswith=PREFIX).exists())

    def test_reports(self):
        results = self.run_scenarios([BY_KEY["cancel"]])
        start = results[0].steps[0].day
        text = report.markdown(results, live=False, start=start)
        self.assertIn("لغو پرونده توسط آموزش", text)
        self.assertIn("دانشجو نمی‌تواند لغو کند", text)
        data = json.loads(report.as_json(results, False, start))
        self.assertTrue(data["scenarios"][0]["ok"])


class DocumentTests(TestCase):
    """سندهای مصنوعی واقعاً همان چیزی را دارند (یا ندارند) که variant می‌گوید."""

    def text(self, **kw):
        upload = documents.proposal_docx(**kw)
        return extract.extract_text(upload)

    def test_complete_document_has_every_section_and_field(self):
        text = self.text()
        for heading in documents._SECTION_HEADINGS.values():
            self.assertIn(heading.split(". ", 1)[1], text)
        self.assertIn("۴۰۰۳۹۹۹۹", text)
        self.assertEqual(documents.expected_missing("complete"), set())

    def test_variants_drop_exactly_what_they_claim(self):
        self.assertNotIn("روش ارزیابی", self.text(variant="no_evaluation"))
        self.assertNotIn("مراجع و منابع", self.text(variant="no_references"))
        self.assertNotIn("student@aut.ac.ir", self.text(variant="empty_student_fields"))
        keys = {item["key"] for item in checklist.ITEMS}
        for variant in documents.VARIANTS:
            self.assertLessEqual(documents.expected_missing(variant), keys, variant)

    def test_topics_differ(self):
        self.assertNotEqual(self.text(topic="databases"), self.text(topic="llm_agents"))


@override_settings(MEDIA_ROOT=MEDIA)
class ShadowReportTests(TestCase):
    def test_human_choice_is_ranked_against_the_agent_suggestion(self):
        from datetime import timedelta

        from agents.models import RefereeSuggestion, SuggestionRun

        from . import shadow

        (result,) = Runner(persist=True).run([BY_KEY["happy_path"]])
        case = ProjectCase.objects.get(pk=result.case_ids[0])
        assigned = case.events.get(action="assign_referees")
        people = {u.username.removeprefix(PREFIX): u for u in User.objects.filter(username__startswith=PREFIX)}
        run = SuggestionRun.objects.create(case=case, purpose="referee", status="done",
                                           finished_at=assigned.created_at - timedelta(minutes=1))
        for rank, name in enumerate(["ref_db", "ref_ai", "ref_net"], 1):
            RefereeSuggestion.objects.create(run=run, referee=people[name], rank=rank, score=1 - rank / 10)

        self.assertEqual(shadow.matching("referee")["n"], 0)          # پرونده‌های سناریو پیش‌فرض حساب نمی‌شوند
        stats = shadow.matching("referee", include_scenarios=True)
        self.assertEqual((stats["n"], stats["hit1"], stats["hit3"], stats["mrr"]), (1, 0.0, 1.0, 0.5))
        self.assertIn("| پیشنهاد داور | 1 | 0٪ | 100٪ | 0.50 |", shadow.markdown(include_scenarios=True))
