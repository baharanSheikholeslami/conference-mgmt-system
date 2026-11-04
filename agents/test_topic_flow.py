"""
آزمون‌های جریان جدید ثبت پرونده:
  ۱. دانشجو بدون موضوع، فقط با حوزه/علایق پرونده می‌سازد
  ۲. سامانه از روی همان متن استاد راهنما پیشنهاد می‌کند (n8n، یا کلیدواژه‌ای اگر در دسترس نباشد)
  ۳. استاد راهنما درخواست را می‌پذیرد (و علایق دانشجو را می‌بیند)
  ۴. با بارگذاری پیشنهاد، موضوع از متن آن استخراج و در پرونده نوشته می‌شود؛ دانشجو/استاد اصلاحش می‌کنند
"""
from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from workflow import services as wf
from workflow.enums import State, TitleSource

from . import keywords, n8n, services, topic
from .models import TopicExtraction
from .tests import PROPOSAL, AgentTestCase, docx, run_of, setUpModule, tearDownModule  # noqa: F401

FA = "سامانه هوشمند مدیریت کنفرانس"
EN = "Intelligent Conference System"


class TitleFromTextTests(SimpleTestCase):
    def test_title_fields_of_the_proposal_form(self):
        found = topic.title_from_text("\n".join(PROPOSAL))
        self.assertEqual((found.title_fa, found.title_en, found.source), (FA, EN, TitleSource.HEADING))

    def test_empty_template_placeholders_are_not_titles(self):
        text = "فرم پیشنهاد پروژه کارشناسی — دانشکده مهندسی کامپیوتر\nعنوان فارسی: ...\nعنوان انگلیسی: ...\n۱. مقدمه"
        self.assertIsNone(topic.title_from_text(text))

    def test_first_title_like_line_is_a_guess(self):
        text = "فرم پیشنهاد پروژه کارشناسی\nتشخیص خودکار اخبار جعلی فارسی\n۱. مقدمه\nمتن مقدمه."
        found = topic.title_from_text(text)
        self.assertEqual((found.title_fa, found.source), ("تشخیص خودکار اخبار جعلی فارسی", TitleSource.FIRST_LINE))

    def test_appears_in_ignores_spacing_and_arabic_letters(self):
        self.assertTrue(topic.appears_in("سامانه  هوشمند مديريت كنفرانس", "عنوان فارسی: " + FA))
        self.assertFalse(topic.appears_in("یک عنوان ساختگی دیگر", "\n".join(PROPOSAL)))


class KeywordRankTests(SimpleTestCase):
    def test_shared_words_rank_and_explain(self):
        rows = keywords.rank("هوش مصنوعی و پردازش زبان طبیعی", [
            {"id": 1, "expertise": "شبکه‌های کامپیوتری، رایانش ابری"},
            {"id": 2, "expertise": "هوش مصنوعی، پردازش زبان طبیعی"},
            {"id": 3, "expertise": ""}])
        by_id = {r["referee_id"]: r for r in rows}
        self.assertGreater(by_id[2]["score"], by_id[1]["score"])
        self.assertEqual(by_id[1]["score"], 0)
        self.assertIsNone(by_id[3]["score"])
        self.assertIn("پردازش", by_id[2]["reason"])


class CaseWithoutTopicTests(AgentTestCase):
    def test_case_is_created_without_topic_and_suggestions_start_at_once(self):
        self.client.force_login(self.student)
        response = self.client.post(reverse("case_create"), {
            "research_area": "هوش مصنوعی", "interests": "عامل‌های مبتنی بر مدل زبانی",
            "academic_year": 1405, "semester": "first"})
        case = self.student.authored_cases.get()
        self.assertRedirects(response, reverse("case_action", args=[case.pk, "select_advisor"]))
        self.assertEqual((case.title_fa, case.title_source), ("", TitleSource.NONE))
        (payload,) = self.calls("suggest-referees")
        self.assertEqual((payload["purpose"], payload["query_text"]),
                         ("advisor", "هوش مصنوعی\nعامل‌های مبتنی بر مدل زبانی"))

    def test_n8n_down_falls_back_to_keyword_ranking(self):
        from workflow.models import User
        User.objects.filter(pk=self.outsider.pk).update(expertise="هوش مصنوعی و عامل‌های هوشمند")
        self.n8n.side_effect = n8n.Unavailable("down")
        case = self.new_case()
        services.request_advisor_suggestions(case)
        run = run_of(case, "advisor")
        self.assertEqual((run.status, run.model), ("done", "keyword-fallback"))
        self.assertEqual(services.suggestion_ranks(case, "advisor")[self.outsider.pk], 1)
        self.client.force_login(self.student)
        page = self.client.get(reverse("case_action", args=[case.pk, "select_advisor"]))
        self.assertContains(page, "تطبیق کلیدواژه‌ای")
        self.assertContains(page, "سرویس هوش مصنوعی در دسترس نبود")

    def test_advisor_sees_the_students_interests_before_accepting(self):
        case = self.do(self.new_case(), "select_advisor", self.student, advisor=self.advisor)
        self.client.force_login(self.advisor)
        page = self.client.get(reverse("case_detail", args=[case.pk]))
        self.assertContains(page, "عامل‌های مبتنی بر مدل زبانی بزرگ")
        self.assertContains(page, "(بی‌عنوان)")
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(reverse("case_detail", args=[case.pk])).status_code, 404)


class TopicExtractionTests(AgentTestCase):
    def setUp(self):
        super().setUp()
        self.case = self.drafting_case()

    def refreshed(self):
        self.case.refresh_from_db()
        return self.case

    def result(self, version, **data):
        return self.api("api_topic_extraction_result", args=(version.topic_extraction.pk,), data=data)

    def test_upload_writes_the_form_title_at_once_and_asks_the_agent(self):
        version = self.upload(self.case)
        case = self.refreshed()
        self.assertEqual((case.title_fa, case.title_en, case.title_source), (FA, EN, TitleSource.HEADING))
        self.assertTrue(case.events.filter(action="extract_title").exists())
        (payload,) = self.calls("extract-topic")
        self.assertEqual(payload["extraction_id"], version.topic_extraction.pk)
        self.assertEqual(payload["heading_title"], {"title_fa": FA, "title_en": EN})
        self.assertIn("۱. مقدمه", payload["document"]["text"])
        self.assertEqual(payload["result_path"],
                         reverse("api_topic_extraction_result", args=[version.topic_extraction.pk]))

    def test_verified_ai_title_is_applied(self):
        paragraphs = ["فرم پیشنهاد پروژه کارشناسی", *PROPOSAL[2:]]          # فرم بدون فیلد عنوان
        version = self.upload(self.case, docx(paragraphs))
        self.assertEqual(self.refreshed().title_source, TitleSource.NONE)
        self.assertEqual(self.result(version, title_fa="سامانه‌ی چندعامله برای مدیریت گردش‌کار",
                                     keywords=["عامل", "گردش‌کار"], model="m").json()["applied"], True)
        case = self.refreshed()
        self.assertEqual((case.title_fa, case.title_source), ("سامانه‌ی چندعامله برای مدیریت گردش‌کار", "ai"))
        run = TopicExtraction.objects.get()
        self.assertEqual((run.status, run.verified, run.keywords), ("done", True, ["عامل", "گردش‌کار"]))

    def test_unverified_ai_title_does_not_replace_the_form_title(self):
        version = self.upload(self.case)
        self.assertFalse(self.result(version, title_fa="عنوانی که مدل از خودش ساخته است").json()["applied"])
        self.assertEqual(self.refreshed().title_fa, FA)
        self.client.force_login(self.student)
        page = self.client.get(reverse("case_detail", args=[self.case.pk]))
        self.assertContains(page, "پیشنهاد عامل: «عنوانی که مدل از خودش ساخته است»")
        self.assertContains(page, "عیناً در متن نیامده")

    def test_manual_title_is_never_overwritten(self):
        self.upload(self.case)
        wf.edit_case(self.case, self.advisor, title_fa="عنوان اصلاح‌شده‌ی استاد", title_en="")
        version = self.upload(self.refreshed(), docx())
        self.result(version, title_fa=FA)
        case = self.refreshed()
        self.assertEqual((case.title_fa, case.title_source), ("عنوان اصلاح‌شده‌ی استاد", TitleSource.MANUAL))

    def test_result_for_an_older_version_is_ignored(self):
        old = self.upload(self.case)
        self.upload(self.refreshed(), docx(["عنوان فارسی: عنوان نسخه‌ی دوم پروژه", *PROPOSAL[2:]]))
        self.assertFalse(self.result(old, title_fa=FA).json()["applied"])
        self.assertEqual(self.refreshed().title_fa, "عنوان نسخه‌ی دوم پروژه")

    def test_n8n_down_keeps_the_form_title(self):
        self.n8n.side_effect = n8n.Unavailable("down")
        version = self.upload(self.case)
        self.assertEqual(self.refreshed().title_fa, FA)
        version.topic_extraction.refresh_from_db()
        self.assertEqual(version.topic_extraction.status, "error")
        self.assertIn("فیلد «عنوان» فرم", version.topic_extraction.error)

    def test_model_error_is_recorded(self):
        version = self.upload(self.case)
        self.result(version, error="مدل زبانی پاسخ نداد.")
        run = TopicExtraction.objects.get()
        self.assertEqual((run.status, run.error), ("error", "مدل زبانی پاسخ نداد."))
        self.assertEqual(self.refreshed().title_fa, FA)

    @override_settings(AGENTS_SHADOW_MODE=True)
    def test_shadow_mode_records_but_does_not_apply(self):
        version = self.upload(self.case, docx(["فرم پیشنهاد پروژه کارشناسی", *PROPOSAL[2:]]))
        self.assertFalse(self.result(version, title_fa="سامانه‌ی چندعامله برای مدیریت گردش‌کار").json()["applied"])
        self.assertEqual(self.refreshed().title_fa, "")

    def test_api_needs_the_token(self):
        version = self.upload(self.case)
        url = reverse("api_topic_extraction_result", args=[version.topic_extraction.pk])
        self.assertEqual(self.client.post(url, "{}", content_type="application/json").status_code, 403)


@override_settings(AGENTS_ENABLED=False)
class TopicWithoutAgentsTests(AgentTestCase):
    def test_form_title_is_still_extracted_and_suggestions_are_keyword_based(self):
        case = self.new_case()
        services.request_advisor_suggestions(case)
        self.assertEqual(run_of(case, "advisor").model, "keyword-fallback")
        case = self.do(case, "select_advisor", self.student, advisor=self.advisor)
        case = self.do(case, "advisor_accept", self.advisor)
        self.upload(case)
        case.refresh_from_db()
        self.assertEqual(case.title_fa, FA)
        self.assertEqual(self.calls("extract-topic"), [])
        self.assertFalse(TopicExtraction.objects.exists())
        self.assertEqual(case.state, State.DRAFTING)
