"""
آزمون‌های رتبه‌بندی استاد راهنما و داور با عامل هوش مصنوعی.

تقسیم کار: پورتال نامزدهای واجد شرایط (ظرفیت، تعارض منافع، گروه) را تعیین می‌کند، عامل n8n
(غربال embedding + نمره‌ی تناسب مدل زبانی) به هر نامزد نمره و دلیل می‌دهد، و پورتال رتبه‌ی نهایی را
از روی همین نمره‌ها می‌سازد. اگر عامل پاسخ ندهد یا دیر کند، رتبه‌بندی کلیدواژه‌ای جایگزین می‌شود.

بخش آخر کد واقعی گره‌های Code گردش‌کار n8n را با Node.js اجرا می‌کند تا قرارداد دو طرف سنجیده شود.
"""
import json
import shutil
import subprocess
import unittest
from datetime import timedelta
from pathlib import Path

from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from workflow.enums import State, UserType
from workflow.forms import AssignRefereesForm
from workflow.models import User
from workflow.statemachine import TRANSITIONS

from . import n8n, services
from .models import SuggestionRun
from .tests import AgentTestCase, run_of

WORKFLOW = Path(__file__).resolve().parent.parent / "n8n" / "workflows" / "03-referee-suggestion.json"


class FinalOrderTests(SimpleTestCase):
    """قاعده‌ی قطعی پورتال برای ساختن رتبه از روی خروجی عامل."""

    def test_ai_score_first_then_similarity_then_missing_profile(self):
        rows = {1: (0.95, None, ""), 2: (0.40, 88, ""), 3: (None, None, ""), 4: (0.70, 91, ""), 5: (0.60, None, "")}
        self.assertEqual([pk for pk, _ in services.final_order(rows, {})], [4, 2, 1, 5, 3])

    def test_ties_go_to_the_professor_with_less_current_load(self):
        rows = {1: (0.90, 80, ""), 2: (0.50, 80, ""), 3: (0.70, 80, "")}
        self.assertEqual([pk for pk, _ in services.final_order(rows, {1: 3, 2: 0, 3: 3})], [2, 1, 3])


class AgentRankingTests(AgentTestCase):
    def to_assignment(self):
        case = self.drafting_case()
        self.upload(case)
        case = self.do(case, "submit_proposal", self.student)
        case = self.do(case, "advisor_approve", self.advisor)
        return self.act(case, "edu_register", self.edu)

    def test_payload_asks_the_agent_to_rank_a_shortlist(self):
        case = self.to_assignment()
        (payload,) = self.calls("suggest-referees")
        self.assertEqual(payload["rerank_top"], services.RERANK_TOP)
        # قواعد قطعی پیش از عامل: راهنما و نویسنده (تعارض منافع) و گروه دیگر نامزد نیستند
        self.assertEqual({c["id"] for c in payload["candidates"]}, {self.ref1.pk, self.ref2.pk, self.head.pk})
        self.assertEqual(run_of(case).status, "pending")

    def test_referees_are_ranked_by_the_agent_score_not_by_similarity(self):
        case = self.to_assignment()
        run = run_of(case)
        response = self.api("api_referee_suggestions_result", args=(run.pk,), data={
            "model": "emb + llm", "suggestions": [
                {"referee_id": self.ref1.pk, "similarity": 0.91, "score": 0.91, "ai_score": 35,
                 "reason": "فقط هم‌حوزه است."},
                {"referee_id": self.ref2.pk, "similarity": 0.62, "score": 0.62, "ai_score": 92,
                 "reason": "تخصص در سامانه‌های چندعامله دقیقاً موضوع پیشنهاد است."},
                {"referee_id": self.head.pk, "similarity": 0.70, "score": 0.70, "ai_score": None, "reason": ""},
                {"referee_id": self.advisor.pk, "similarity": 0.99, "ai_score": 100, "reason": "تعارض منافع"}]})
        self.assertEqual(response.json(), {"status": "done", "saved": 3})
        rows = list(run.suggestions.values_list("referee_id", "rank", "ai_score", "similarity", "score"))
        self.assertEqual(rows, [(self.ref2.pk, 1, 92, 0.62, 0.92), (self.ref1.pk, 2, 35, 0.91, 0.35),
                                (self.head.pk, 3, None, 0.70, 0.70)])
        form = AssignRefereesForm(case=case, transition=TRANSITIONS["assign_referees"])
        self.assertEqual(list(form.fields["referees"].queryset), [self.ref2, self.ref1, self.head])

        self.client.force_login(self.head)
        page = self.client.get(reverse("case_action", args=[case.pk, "assign_referees"]))
        self.assertContains(page, "تناسب با موضوع از نگاه عامل ۹۲٪")
        self.assertContains(page, "شباهت معنایی ۶۲٪")
        self.assertContains(page, "دلیل عامل")
        self.assertContains(page, "تخصص در سامانه‌های چندعامله دقیقاً موضوع پیشنهاد است.")
        self.assertContains(page, "رتبه‌بندی را عامل هوش مصنوعی انجام داده است")
        self.assertNotContains(page, "checked")
        case.refresh_from_db()
        self.assertEqual(case.state, State.REFEREE_ASSIGNMENT)          # عامل خودش داوری تعیین نکرد

    def test_out_of_range_agent_scores_are_clamped(self):
        case = self.to_assignment()
        run = run_of(case)
        self.api("api_referee_suggestions_result", args=(run.pk,), data={"suggestions": [
            {"referee_id": self.ref1.pk, "similarity": 0.5, "ai_score": 140},
            {"referee_id": self.ref2.pk, "similarity": 0.5, "ai_score": -3},
            {"referee_id": self.head.pk, "similarity": 0.5, "ai_score": True}]})
        self.assertEqual(list(run.suggestions.values_list("referee_id", "ai_score")),
                         [(self.ref1.pk, 100), (self.ref2.pk, 0), (self.head.pk, None)])

    def test_equal_agent_scores_prefer_the_less_busy_advisor(self):
        other = User.objects.create_user("student2", password="pass12345", user_type=UserType.STUDENT)
        busy = self.do(self.new_case(other), "select_advisor", other, advisor=self.advisor)
        self.assertEqual(busy.advisor, self.advisor)
        case = self.new_case()
        services.request_advisor_suggestions(case)
        run = run_of(case, "advisor")
        self.api("api_referee_suggestions_result", args=(run.pk,), data={"suggestions": [
            {"referee_id": self.advisor.pk, "similarity": 0.8, "ai_score": 85, "reason": "الف"},
            {"referee_id": self.outsider.pk, "similarity": 0.6, "ai_score": 85, "reason": "ب"}]})
        self.assertEqual(services.suggestion_ranks(case, "advisor"), {self.outsider.pk: 1, self.advisor.pk: 2})
        self.client.force_login(self.student)
        page = self.client.get(reverse("case_action", args=[case.pk, "select_advisor"]))
        self.assertContains(page, "تناسب با علایق شما از نگاه عامل ۸۵٪")
        self.assertContains(page, "دانشجویان در جریان: ۱")

    def test_llm_failure_keeps_the_similarity_order_with_a_note(self):
        case = self.to_assignment()
        run = run_of(case)
        self.api("api_referee_suggestions_result", args=(run.pk,), data={
            "model": "emb + llm", "rerank_error": "مدل زبانی پاسخ نداد: timeout", "suggestions": [
                {"referee_id": self.ref1.pk, "similarity": 0.3, "ai_score": None, "reason": ""},
                {"referee_id": self.ref2.pk, "similarity": 0.8, "ai_score": None, "reason": ""}]})
        run.refresh_from_db()
        self.assertEqual((run.status, run.model), ("done", "emb + llm"))
        self.assertIn("عامل هوش مصنوعی نمره‌ی تناسب نداد", run.error)
        self.assertEqual(services.suggestion_ranks(case), {self.ref2.pk: 1, self.ref1.pk: 2})
        self.client.force_login(self.head)
        page = self.client.get(reverse("case_action", args=[case.pk, "assign_referees"]))
        self.assertContains(page, "ترتیب فقط بر پایه‌ی شباهت معنایی است")
        self.assertContains(page, "شباهت موضوعی ۸۰٪")

    def test_no_answer_in_time_falls_back_to_keyword_ranking(self):
        User.objects.filter(pk=self.ref1.pk).update(expertise="سامانه‌ی چندعامله و گردش‌کار")
        case = self.to_assignment()
        run = run_of(case)
        SuggestionRun.objects.filter(pk=run.pk).update(requested_at=timezone.now() - timedelta(minutes=11))
        self.client.force_login(self.head)
        page = self.client.get(reverse("case_action", args=[case.pk, "assign_referees"]))
        run.refresh_from_db()
        self.assertEqual((run.status, run.model, run.error), ("done", "keyword-fallback", services.STALE_NOTE))
        self.assertContains(page, services.STALE_NOTE)
        self.assertContains(page, "تطبیق کلیدواژه‌ای")
        self.assertEqual(services.suggestion_ranks(case)[self.ref1.pk], 1)
        # اگر پاسخ عامل بعداً برسد، جای رتبه‌بندی کلیدواژه‌ای را می‌گیرد
        self.api("api_referee_suggestions_result", args=(run.pk,), data={"model": "emb + llm", "suggestions": [
            {"referee_id": self.ref2.pk, "similarity": 0.5, "ai_score": 90, "reason": "دیر رسید"}]})
        run.refresh_from_db()
        self.assertEqual((run.model, run.error), ("emb + llm", ""))
        self.assertEqual(services.suggestion_ranks(case), {self.ref2.pk: 1})

    def test_a_pending_run_inside_the_deadline_is_left_waiting(self):
        case = self.to_assignment()
        self.assertEqual(services.suggestion_ranks(case), {})
        self.assertEqual(run_of(case).status, "pending")

    def test_n8n_down_still_uses_the_keyword_fallback(self):
        self.n8n.side_effect = n8n.Unavailable("down")
        case = self.new_case()
        run = services.request_advisor_suggestions(case)
        self.assertEqual((run.status, run.model), ("done", "keyword-fallback"))
        self.assertTrue(run.suggestions.exists())


NODE_HARNESS = r"""
const fs = require('fs');
const [wfPath, inputPath] = process.argv.slice(1);
const wf = JSON.parse(fs.readFileSync(wfPath, 'utf8'));
const input = JSON.parse(fs.readFileSync(inputPath, 'utf8'));
const code = Object.fromEntries(wf.nodes.filter((n) => n.parameters.jsCode).map((n) => [n.name, n.parameters.jsCode]));
const $env = { AGENT_TOKEN: 't', EMBEDDING_MODEL: 'emb', LLM_CHAT_MODEL: 'llm' };
const out = {};
function run(name, json) {
  const $ = (n) => ({ first: () => ({ json: out[n] }) });
  const $input = { first: () => ({ json }) };
  out[name] = new Function('$', '$input', '$env', code[name])($, $input, $env)[0].json;
  return out[name];
}
run('Prepare Candidates', { headers: { 'x-agent-token': 't' }, body: input.body });
run('Shortlist By Similarity', input.embeddings);
run('Build Suggestions', input.llm);
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "Node.js نصب نیست")
class WorkflowCodeTests(SimpleTestCase):
    """کد واقعی گره‌های Code گردش‌کار suggest-referees، با embedding و پاسخ مدل ساختگی."""

    BODY = {"run_id": 7, "purpose": "referee", "result_path": "/api/agents/referee-suggestions/7/result/",
            "query_text": "سامانه‌ی چندعامله", "rerank_top": 2, "explain_top": 5,
            "candidates": [{"id": 3, "name": "الف", "expertise": "x"}, {"id": 1, "name": "ب", "expertise": "y"},
                           {"id": 2, "name": "پ", "expertise": "z"}, {"id": 9, "name": "ت", "expertise": ""}]}
    EMBEDDINGS = {"data": [{"index": 0, "embedding": [1, 0]}, {"index": 1, "embedding": [1, 0.1]},
                           {"index": 2, "embedding": [0, 1]}, {"index": 3, "embedding": [1, 1]}]}

    def run_workflow(self, llm, embeddings=None):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"body": self.BODY, "embeddings": embeddings or self.EMBEDDINGS, "llm": llm}, f)
        done = subprocess.run(["node", "-e", NODE_HARNESS, str(WORKFLOW), f.name],
                              capture_output=True, text=True, timeout=30, check=True)
        Path(f.name).unlink()
        return json.loads(done.stdout)

    def llm(self, content):
        return {"choices": [{"message": {"content": content}}]}

    def test_only_the_shortlist_goes_to_the_llm_in_id_order(self):
        out = self.run_workflow(self.llm("{}"))
        shortlist = out["Shortlist By Similarity"]
        self.assertEqual(shortlist["shortlist_ids"], [2, 3])           # دو نفر نزدیک‌تر، به ترتیب شناسه
        request = shortlist["llm_request"]
        self.assertEqual((request["temperature"], request["response_format"]), (0, {"type": "json_object"}))
        user = request["messages"][1]["content"]
        self.assertLess(user.index("id=2"), user.index("id=3"))
        self.assertNotIn("id=1 ", user)
        self.assertIn('"rankings"', request["messages"][0]["content"])

    def test_agent_scores_and_reasons_reach_the_portal(self):
        content = ('<think>…</think>{"rankings": [{"id": 3, "score": 40, "reason": "هم‌حوزه"}, '
                   '{"id": 2, "score": "91", "reason": "دقیقاً همان موضوع"}, {"id": 1, "score": 99, "reason": "نه"}]}')
        result = self.run_workflow(self.llm(content))["Build Suggestions"]["result"]
        rows = {s["referee_id"]: s for s in result["suggestions"]}
        self.assertNotIn("rerank_error", result)
        self.assertEqual((rows[2]["ai_score"], rows[2]["reason"]), (91, "دقیقاً همان موضوع"))
        self.assertEqual(rows[3]["ai_score"], 40)
        self.assertIsNone(rows[1]["ai_score"])                       # خارج از فهرست کوتاه
        self.assertIsNone(rows[9]["similarity"])                     # تخصص ثبت نشده
        self.assertEqual(result["model"], "emb + llm")

    def test_llm_failure_is_reported_as_rerank_error(self):
        for llm in ({"error": {"message": "timeout"}}, self.llm("نمی‌دانم")):
            result = self.run_workflow(llm)["Build Suggestions"]["result"]
            self.assertIn("rerank_error", result)
            self.assertTrue(all(s["ai_score"] is None for s in result["suggestions"]))
            self.assertEqual(len(result["suggestions"]), 4)

    def test_embedding_failure_is_reported_as_error(self):
        result = self.run_workflow(self.llm("{}"), embeddings={"error": "down"})["Build Suggestions"]["result"]
        self.assertEqual(result["error"], "مدل embedding پاسخ نداد.")
