"""
آزمون‌های عامل ۵ (آماده‌سازی دفاع). مثل tests.py، n8n ساختگی است (agents.n8n.post با mock جایگزین
می‌شود) و قرارداد دو طرف سنجیده می‌شود: چه چیزی برای n8n می‌رود و پورتال از پاسخ آن چه می‌پذیرد.
"""
from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from workflow import services as wf
from workflow.enums import DocKind, State

from . import defense, n8n, services
from .models import DefensePrep, MockAnswer
from .tests import TOKEN, AgentTestCase, docx

METHOD = "برای رتبه‌بندی داوران از شباهت کسینوسی میان بردار موضوع و بردار تخصص استادان استفاده شد."
RESULT = "دقت پیشنهاد داور در بیست سناریوی آزمایشی هشتاد و پنج درصد به دست آمد."
LIMIT = "مجموعه‌ی آزمون فقط شامل پرونده‌های یک دانشکده بود و تعمیم آن بررسی نشده است."
FILLER = "این فصل جزئیات طراحی سامانه و گردش‌کار پرونده‌ها را شرح می‌دهد و نمونه‌هایی از اجرا را نشان می‌دهد. "
THESIS = ["چکیده", FILLER * 3, "فصل ۳ — روش", FILLER * 2, METHOD, FILLER * 2,
          "فصل ۴ — ارزیابی", RESULT, FILLER, "فصل ۵ — جمع‌بندی", LIMIT, FILLER]
PROMISE = "سامانه با کاربران واقعی دانشکده در یک نیم‌سال کامل آزموده خواهد شد."


class QuoteMatchingTests(SimpleTestCase):
    def test_exact_match_ignores_spacing_letters_digits_and_punctuation(self):
        text = "مقدمه\nدقت مدل در ۲۰ سناریو، ٨٥ درصد بود (جدول ۴-۱).\nپایان"
        at = defense.find_quote(text, "دقت مدل در 20 سناريو 85 درصد بود")
        self.assertEqual(text[at:at + 3], "دقت")

    def test_half_space_variants_match(self):
        text = "نتیجه‌ی آزمایش‌ها نشان داد که روش پیشنهادی بهتر است."
        self.assertIsNotNone(defense.find_quote(text, "نتیجه ی آزمایش ها نشان داد که روش"))
        self.assertIsNotNone(defense.find_quote(text, "نتیجهی آزمایشها نشان داد که روش"))

    def test_fuzzy_match_tolerates_one_changed_word(self):
        text = f"{FILLER}{METHOD} {FILLER}"
        at = defense.find_quote(text, "برای رتبه‌بندی داوران از شباهت کسینوسی بین بردار موضوع و بردار تخصص")
        self.assertIsNotNone(at)
        self.assertTrue(text[at:].startswith("برای رتبه‌بندی"))

    def test_invented_or_too_short_quotes_are_rejected(self):
        text = f"{FILLER}{METHOD}"
        self.assertIsNone(defense.find_quote(text, "از شبکه‌ی عصبی گرافی با سه لایه‌ی توجه برای رتبه‌بندی استفاده شد"))
        self.assertIsNone(defense.find_quote(text, "روش"))
        self.assertIsNone(defense.find_quote("", METHOD))

    def test_context_is_taken_around_the_quote(self):
        text = "x" * 5000 + METHOD + "y" * 5000
        at = defense.find_quote(text, METHOD)
        context = defense.context_around(text, at, size=900)
        self.assertIn(METHOD, context)
        self.assertEqual(len(context), 900)
        self.assertEqual(defense.context_around(text, None, size=10), "x" * 10)


class DefenseTestCase(AgentTestCase):
    def thesis_case(self, upload=True):
        """پرونده‌ای در «اجرای پروژه» با پیشنهاد و (به‌طور پیش‌فرض) یک نسخه‌ی پایان‌نامه."""
        case = self.drafting_case()
        self.upload(case, docx(paragraphs=["۱. مقدمه", PROMISE + " " + FILLER * 3], table=None))
        case = self.do(case, "submit_proposal", self.student)
        case = self.do(case, "advisor_approve", self.advisor)
        case = self.do(case, "edu_register", self.edu)
        case = self.do(case, "assign_referees", self.head, referees=[self.ref1])
        case = self.do(case, "submit_review", self.ref1, decision="approve")
        case = self.do(case, "council_approve", self.edu)
        self.assertEqual(case.state, State.IN_PROGRESS)
        if upload:
            self.upload_thesis(case)
        return case

    def upload_thesis(self, case, paragraphs=THESIS):
        with self.captureOnCommitCallbacks(execute=True):
            return wf.upload_document(case, self.student, DocKind.THESIS,
                                      docx(paragraphs=paragraphs, table=None, name="thesis.docx"))

    def start(self, case, user=None):
        self.client.force_login(user or self.student)
        return self.client.post(reverse("defense_prep_start", args=[case.pk]))

    def result(self, prep, **data):
        return self.api("api_defense_prep_result", args=(prep.pk,), data=data)

    def ready_prep(self, case):
        self.start(case)
        prep = DefensePrep.objects.get(version__case=case)
        self.result(prep, model="m", questions=[
            {"category": "method", "question": "چرا شباهت کسینوسی را برای رتبه‌بندی داوران برگزیدید؟",
             "why": "انتخاب معیار شباهت توجیه نشده است.", "evidence": METHOD},
            {"category": "evaluation", "question": "بیست سناریو برای ادعای دقت هشتاد و پنج درصد کافی است؟",
             "evidence": RESULT}])
        prep.refresh_from_db()
        return prep


class DefensePrepRequestTests(DefenseTestCase):
    def test_student_starts_it_and_n8n_gets_thesis_proposal_and_categories(self):
        case = self.thesis_case()
        response = self.start(case)
        self.assertRedirects(response, reverse("defense_prep", args=[case.pk]))
        (body,) = self.calls("defense-prep")
        prep = DefensePrep.objects.get(pk=body["prep_id"])
        self.assertEqual(prep.version, wf.latest_version(case, DocKind.THESIS))
        self.assertTrue(prep.is_waiting)
        self.assertIn(METHOD, body["thesis"]["text"])
        self.assertIn(PROMISE, body["proposal"]["excerpt"])
        self.assertEqual([c["key"] for c in body["categories"]], defense.CATEGORY_KEYS)
        self.assertEqual(body["result_path"], reverse("api_defense_prep_result", args=[prep.pk]))
        self.assertEqual(body["max_questions"], defense.MAX_QUESTIONS)

    def test_it_never_runs_by_itself_on_upload(self):
        self.thesis_case()
        self.assertEqual(self.calls("defense-prep"), [])

    def test_only_the_author_sees_it_and_only_after_a_thesis_exists(self):
        case = self.thesis_case(upload=False)
        self.client.force_login(self.student)
        self.assertNotContains(self.client.get(reverse("case_detail", args=[case.pk])), "آماده‌سازی دفاع")
        self.assertEqual(self.start(case).status_code, 403)
        self.upload_thesis(case)
        self.client.force_login(self.student)
        self.assertContains(self.client.get(reverse("case_detail", args=[case.pk])), "آماده‌سازی دفاع")
        for user in (self.advisor, self.ref1, self.head, self.edu):
            self.client.force_login(user)
            self.assertNotContains(self.client.get(reverse("case_detail", args=[case.pk])), "defense-card", msg_prefix=user.username)
            self.assertEqual(self.client.get(reverse("defense_prep", args=[case.pk])).status_code, 404, user.username)
            self.assertEqual(self.start(case, user).status_code, 404, user.username)

    def test_n8n_down_or_unreadable_thesis_becomes_an_agent_error(self):
        case = self.thesis_case()
        self.n8n.side_effect = n8n.Unavailable("down")
        self.start(case)
        prep = DefensePrep.objects.get(version__case=case)
        self.assertEqual(prep.status, DefensePrep.Status.ERROR)
        self.assertIn("در دسترس نیست", prep.error)
        self.n8n.side_effect = None
        self.upload_thesis(case, paragraphs=["کوتاه"])
        before = len(self.calls("defense-prep"))
        self.start(case)
        prep = DefensePrep.objects.get(version=wf.latest_version(case, DocKind.THESIS))
        self.assertIn("متن قابل‌خواندنی", prep.error)
        self.assertEqual(len(self.calls("defense-prep")), before)

    def test_it_is_advice_only_and_never_changes_the_case(self):
        case = self.thesis_case()
        events = case.events.count()
        self.ready_prep(case)
        case.refresh_from_db()
        self.assertEqual(case.state, State.IN_PROGRESS)
        self.assertEqual(case.events.count(), events)

    def test_closed_cases_have_no_defense_prep(self):
        case = self.thesis_case()
        case.state = State.COMPLETED
        case.save()
        self.assertIsNone(services.defense_card(case, self.student))
        self.assertEqual(self.start(case).status_code, 403)


class DefensePrepResultTests(DefenseTestCase):
    def test_quotes_are_verified_against_the_real_thesis_and_proposal(self):
        case = self.thesis_case()
        self.start(case)
        prep = DefensePrep.objects.get(version__case=case)
        response = self.result(prep, model="gemma", windows=3, failed_windows=1, questions=[
            {"category": "evaluation", "question": "بیست سناریو برای ادعای دقت هشتاد و پنج درصد کافی است؟",
             "why": "حجم آزمون کم است.", "evidence": RESULT},
            {"category": "method", "question": "چرا از شبکه‌ی عصبی گرافی استفاده کردید؟",
             "evidence": "از شبکه‌ی عصبی گرافی با سه لایه‌ی توجه برای رتبه‌بندی استفاده شد"},
            {"category": "proposal_gap", "question": "آزمون با کاربران واقعی در یک نیم‌سال چه شد؟",
             "evidence": PROMISE},
            {"category": "made_up", "question": "اگر دوباره شروع می‌کردید چه چیزی را تغییر می‌دادید؟"},
            {"category": "method", "question": "  بیست سناریو برای ادعای دقت هشتاد و پنج درصد کافی است؟ "},
            {"category": "method", "question": "کوتاه"}, "not a dict"],
            weak_points=[{"title": "تعمیم‌پذیری بررسی نشده", "severity": "high", "evidence": LIMIT,
                          "detail": "فقط یک دانشکده.", "suggestion": "محدودیت را صریح بگویید."},
                         {"title": "ادعای ساختگی", "severity": "critical", "evidence": "جمله‌ای که در متن نیست اصلاً و ابداً"}])
        self.assertEqual(response.json()["status"], "done")
        prep.refresh_from_db()
        by_text = {q["question"]: q for q in prep.questions}
        self.assertEqual(len(prep.questions), 4)          # تکراری و کوتاه حذف شد
        real = by_text["بیست سناریو برای ادعای دقت هشتاد و پنج درصد کافی است؟"]
        self.assertEqual((real["verified"], real["source"]), (True, "thesis"))
        self.assertIsNotNone(real["anchor"])
        fake = by_text["چرا از شبکه‌ی عصبی گرافی استفاده کردید؟"]
        self.assertEqual((fake["verified"], fake["source"], fake["anchor"]), (False, "", None))
        gap = by_text["آزمون با کاربران واقعی در یک نیم‌سال چه شد؟"]
        self.assertEqual((gap["verified"], gap["source"]), (True, "proposal"))
        self.assertEqual(by_text["اگر دوباره شروع می‌کردید چه چیزی را تغییر می‌دادید؟"]["category"], defense.GENERAL)
        # به ترتیب دسته‌ها و با شناسه‌ی پایدار
        self.assertEqual([q["category"] for q in prep.questions], ["method", "evaluation", "proposal_gap", "general"])
        self.assertEqual([q["id"] for q in prep.questions], ["q1", "q2", "q3", "q4"])
        self.assertEqual([(w["title"], w["verified"], w["severity"]) for w in prep.weak_points],
                         [("تعمیم‌پذیری بررسی نشده", True, "high"), ("ادعای ساختگی", False, "medium")])
        self.assertIn("۱ تکه از ۳", prep.error)
        self.assertEqual(prep.model, "gemma")

    def test_counts_are_capped_and_verified_items_win(self):
        case = self.thesis_case()
        self.start(case)
        prep = DefensePrep.objects.get(version__case=case)
        made_up = [{"category": "method", "question": f"پرسش ساختگی شماره‌ی {i} درباره‌ی روش؟",
                    "evidence": f"نقل‌قول ساختگی شماره {i} که در متن پایان‌نامه وجود ندارد"} for i in range(20)]
        real = {"category": "limitations", "question": "تعمیم نتایج به دانشکده‌های دیگر را چگونه نشان می‌دهید؟",
                "evidence": LIMIT}
        self.result(prep, questions=made_up + [real])
        prep.refresh_from_db()
        self.assertEqual(len(prep.questions), defense.MAX_QUESTIONS)
        self.assertIn(real["question"], [q["question"] for q in prep.questions])

    def test_errors_and_empty_results(self):
        case = self.thesis_case()
        self.start(case)
        prep = DefensePrep.objects.get(version__case=case)
        self.result(prep, error="مدل زبانی پاسخ نداد.", detail="timeout")
        prep.refresh_from_db()
        self.assertEqual((prep.status, prep.error), ("error", "مدل زبانی پاسخ نداد."))
        self.result(prep, questions=[], weak_points=[])
        prep.refresh_from_db()
        self.assertEqual(prep.status, "error")
        self.assertEqual(self.api("api_defense_prep_result", args=(9999,), data={}).status_code, 404)

    def test_new_endpoints_require_the_shared_token(self):
        for name in ("api_defense_prep_result", "api_defense_mock_result"):
            self.assertEqual(self.api(name, args=(1,), token="wrong").status_code, 403, name)
            self.assertEqual(self.api(name, args=(1,), token="").status_code, 403, name)

    def test_page_shows_questions_weak_points_and_evidence_labels(self):
        case = self.thesis_case()
        prep = self.ready_prep(case)
        prep.weak_points = [{"title": "تعمیم‌پذیری بررسی نشده", "severity": "high", "detail": "", "suggestion": "",
                             "evidence": "نقل ساختگی", "source": "", "verified": False}]
        prep.save()
        self.client.force_login(self.student)
        page = self.client.get(reverse("defense_prep", args=[case.pk]))
        self.assertContains(page, "چرا شباهت کسینوسی را برای رتبه‌بندی داوران برگزیدید؟")
        self.assertContains(page, "روش و طراحی")
        self.assertContains(page, "از متن پایان‌نامه")
        self.assertContains(page, "شاهد در متن پیدا نشد")
        self.assertContains(page, "تعمیم‌پذیری بررسی نشده")
        self.assertContains(page, 'name="answer"', count=2)
        self.assertNotContains(page, "data-agent-waiting")

    def test_waiting_page_refreshes_itself_and_cannot_be_started_twice(self):
        case = self.thesis_case()
        self.start(case)
        self.assertContains(self.client.get(reverse("defense_prep", args=[case.pk])), "data-agent-waiting")
        self.start(case)
        self.assertEqual(len(self.calls("defense-prep")), 1)

    def test_a_new_thesis_version_marks_the_list_outdated(self):
        case = self.thesis_case()
        prep = self.ready_prep(case)
        self.upload_thesis(case)
        self.client.force_login(self.student)
        self.assertContains(self.client.get(reverse("case_detail", args=[case.pk])), "آماده‌سازی برای نسخه‌ی ۲")
        self.assertFalse(services.can_practice(prep, self.student))


class MockSessionTests(DefenseTestCase):
    def answer(self, prep, qid="q1", text="من شباهت کسینوسی را چون به طول بردار حساس نیست انتخاب کردم.", **extra):
        self.client.force_login(self.student)
        return self.client.post(reverse("defense_mock_answer", args=[prep.pk]),
                                {"question_id": qid, "answer": text, **extra})

    def test_answer_goes_to_n8n_with_the_thesis_passage_the_question_came_from(self):
        case = self.thesis_case()
        prep = self.ready_prep(case)
        response = self.answer(prep)
        self.assertRedirects(response, reverse("defense_prep", args=[case.pk]) + "#q1")
        (body,) = self.calls("defense-mock")
        mock = MockAnswer.objects.get(pk=body["answer_id"])
        self.assertEqual(body["question"], prep.questions[0]["question"])
        self.assertIn(METHOD, body["thesis_excerpt"])
        self.assertEqual(body["category"], "روش و طراحی")
        self.assertEqual(body["result_path"], reverse("api_defense_mock_result", args=[mock.pk]))
        self.assertTrue(mock.is_waiting)

    def test_feedback_is_saved_and_the_follow_up_can_be_answered(self):
        case = self.thesis_case()
        prep = self.ready_prep(case)
        self.answer(prep)
        mock = MockAnswer.objects.get()
        self.api("api_defense_mock_result", args=(mock.pk,), data={
            "verdict": "partial", "feedback": "درست است اما مقایسه با معیار دیگری نکردید.",
            "missing": ["مقایسه با فاصله‌ی اقلیدسی", "", 3], "follow_up": "آیا معیار دیگری را آزمودید؟", "model": "m"})
        mock.refresh_from_db()
        self.assertEqual((mock.status, mock.verdict), ("done", "partial"))
        self.assertEqual(mock.missing, ["مقایسه با فاصله‌ی اقلیدسی"])
        page = self.client.get(reverse("defense_prep", args=[case.pk]))
        self.assertContains(page, "پاسخ ناقص")
        self.assertContains(page, "آیا معیار دیگری را آزمودید؟")
        self.answer(prep, text="بله، فاصله‌ی اقلیدسی را هم آزمودم و نتیجه‌ی بدتری داشت.", follow_up_of=mock.pk)
        body = self.calls("defense-mock")[-1]
        self.assertEqual(body["question"], "آیا معیار دیگری را آزمودید؟")
        self.assertEqual(body["previous_turns"][0]["answer"], mock.answer)
        self.assertEqual(MockAnswer.objects.get(pk=body["answer_id"]).follow_up_of, mock)

    def test_bad_answers_and_bad_feedback_are_rejected(self):
        case = self.thesis_case()
        prep = self.ready_prep(case)
        self.answer(prep, text="کوتاه")
        self.answer(prep, qid="q99")
        self.assertEqual(MockAnswer.objects.count(), 0)
        self.answer(prep)
        self.answer(prep, qid="q2", text="پاسخ دوم در حالی که ارزیابی اولی هنوز تمام نشده است.")
        self.assertEqual(MockAnswer.objects.count(), 1)          # یک ارزیابی در جریان در هر زمان
        mock = MockAnswer.objects.get()
        self.api("api_defense_mock_result", args=(mock.pk,), data={"verdict": "great", "feedback": ""})
        mock.refresh_from_db()
        self.assertEqual(mock.status, "error")
        self.api("api_defense_mock_result", args=(mock.pk,), data={"verdict": "great", "feedback": "خوب"})
        mock.refresh_from_db()
        self.assertEqual((mock.status, mock.verdict), ("done", ""))
        self.assertEqual(self.answer(prep, follow_up_of="abc").status_code, 404)

    def test_only_the_author_can_practice(self):
        case = self.thesis_case()
        prep = self.ready_prep(case)
        for user in (self.advisor, self.ref1, self.edu):
            self.client.force_login(user)
            response = self.client.post(reverse("defense_mock_answer", args=[prep.pk]),
                                        {"question_id": "q1", "answer": "پاسخی به جای دانشجو که نباید پذیرفته شود."})
            self.assertIn(response.status_code, (403, 404), user.username)
        self.assertEqual(MockAnswer.objects.count(), 0)

    def test_n8n_down_marks_the_answer_failed(self):
        case = self.thesis_case()
        prep = self.ready_prep(case)
        self.n8n.side_effect = n8n.Unavailable("down")
        self.answer(prep)
        self.assertEqual(MockAnswer.objects.get().status, "error")

    def test_regenerating_clears_old_practice(self):
        case = self.thesis_case()
        prep = self.ready_prep(case)
        self.answer(prep)
        MockAnswer.objects.update(status="done", feedback="خوب")
        self.start(case)
        self.assertEqual(MockAnswer.objects.count(), 0)


@override_settings(AGENTS_SHADOW_MODE=True)
class DefenseShadowModeTests(DefenseTestCase):
    def test_in_shadow_mode_only_edu_sees_and_runs_it(self):
        case = self.thesis_case()
        self.assertEqual(self.start(case).status_code, 404)
        self.assertEqual(self.start(case, self.edu).status_code, 302)
        self.assertEqual(len(self.calls("defense-prep")), 1)
        prep = DefensePrep.objects.get()
        self.assertFalse(services.can_practice(prep, self.edu))
