"""
اجراکننده‌ی سناریوهای سرتاسری (فاز ۴).

دو حالت اجرا:
  • آفلاین (پیش‌فرض) — فقط موتور گردش‌کار و قواعد مهلت؛ عامل‌ها خاموش‌اند. سریع و قطعی است و
    همان چیزی است که integration/tests.py در هر بار `manage.py test` اجرا می‌کند.
  • زنده (live=True) — پورتال واقعاً با n8n و مدل زبانی صحبت می‌کند. پس از هر گام که عاملی را
    خبر می‌کند، اجراکننده منتظر پاسخ آن عامل می‌ماند و خروجی را با «پاسخ درست» مقایسه می‌کند.
    نتیجه‌ی گردش‌کار باید قطعی درست باشد (✓/✗)؛ خروجی عامل‌ها «مشاهده» است و برای ارزیابی فاز ۵
    ثبت می‌شود، چون مدل زبانی ممکن است خطا کند.

ساعت سامانه شبیه‌سازی می‌شود: هر گام در روز خودش اجرا می‌شود (django.utils.timezone.localdate
وصله می‌شود)، پس یک سناریوی ۱۳۰روزه در چند ثانیه اجرا می‌شود.

اصل انسان‌در‌حلقه این‌جا هم رعایت شده است: همه‌ی گذارها با perform() و به نام یک بازیگر انسانی
انجام می‌شوند؛ اجراکننده هیچ‌گاه پیشنهاد عامل را خودکار نمی‌پذیرد.
"""
import tempfile
import time
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import date, timedelta
from unittest import mock

from django.db import transaction
from django.test.utils import override_settings
from django.utils import timezone

from agents import services as agents
from agents.models import DocumentValidation, Notification, SuggestionRun
from workflow import services as wf
from workflow.enums import DocKind, State, UserType
from workflow.models import Department, ProjectCase, Review, User

from . import documents
from .scenarios import GUARD, NOT_ALLOWED, SCENARIOS, Scenario, Step

PREFIX = "e2e_"
DEPARTMENT = "سناریوهای آزمایشی"
TITLE_TAG = "[سناریو] "

# بازیگران ثابت همه‌ی سناریوها (نویسنده برای هر سناریو جدا ساخته می‌شود)
CAST = {
    "@advisor": ("advisor", "استاد", "راهنمای آزمایشی", UserType.FACULTY, False,
                 "پردازش زبان طبیعی، مدل‌های زبانی بزرگ، عامل‌های هوشمند و سامانه‌های پرسش و پاسخ"),
    "@ref_ai": ("ref_ai", "داور", "هوش مصنوعی", UserType.FACULTY, False,
                "یادگیری ماشین، بازیابی اطلاعات، جست‌وجوی معنایی و مدل‌های زبانی"),
    "@ref_db": ("ref_db", "داور", "پایگاه‌داده", UserType.FACULTY, False,
                "پایگاه‌داده‌های توزیع‌شده، مدیریت تراکنش، مهندسی نرم‌افزار"),
    "@ref_net": ("ref_net", "داور", "شبکه", UserType.FACULTY, False,
                 "شبکه‌های کامپیوتری، امنیت شبکه، رایانش ابری"),
    "@head": ("head", "مدیر", "گروه آزمایشی", UserType.FACULTY, True,
              "معماری کامپیوتر، سیستم‌های نهفته، طراحی سخت‌افزار"),
    "@edu": ("edu", "کارشناس", "آموزش آزمایشی", UserType.EDU_STAFF, False, ""),
}


# ───────────────────────── نتیجه‌ها ─────────────────────────

@dataclass
class StepResult:
    day: date
    actor: str
    action: str
    expected: str
    got: str
    ok: bool
    detail: str = ""
    note: str = ""


@dataclass
class Observation:
    """خروجی یک عامل در اجرای زنده، در کنار پاسخ درست (داده‌ی خام ارزیابی فاز ۵)."""
    agent: str
    expected: str
    got: str
    match: object          # True / False / None (پاسخی نرسید)
    seconds: float = 0.0
    detail: str = ""


@dataclass
class ScenarioResult:
    scenario: Scenario
    steps: list = field(default_factory=list)
    observations: list = field(default_factory=list)
    case_ids: list = field(default_factory=list)
    error: str = ""

    @property
    def ok(self):
        return not self.error and all(s.ok for s in self.steps)

    @property
    def passed(self):
        return sum(s.ok for s in self.steps)


class Rollback(Exception):
    """برای برگرداندن همه‌ی تغییرهای اجرای آفلاین در پایان."""


# ───────────────────────── اجراکننده ─────────────────────────

class Runner:
    def __init__(self, live=False, start=None, agent_timeout=300, persist=False, log=None):
        self.live = live
        self.start = start or timezone.localdate()
        self.agent_timeout = agent_timeout
        self.persist = persist or live      # اجرای زنده باید commit کند تا n8n پاسخ را به پورتال برگرداند
        self.log = log or (lambda text: None)

    # ── بازیگران ──
    def cast(self, scenario):
        dept, _ = Department.objects.get_or_create(name=DEPARTMENT)
        people = {}
        for key, (name, first, last, utype, is_head, expertise) in CAST.items():
            people[key] = self._user(f"{PREFIX}{name}", first, last, utype,
                                     dept if utype == UserType.FACULTY else None, is_head, expertise)
        author_type = UserType.EXTERNAL if scenario.author_type == "external" else UserType.STUDENT
        people["@author"] = self._user(f"{PREFIX}{scenario.key}", "نویسنده", f"سناریوی {scenario.key}",
                                       author_type, None, False, "")
        return people

    @staticmethod
    def _user(username, first, last, utype, dept, is_head, expertise):
        user, created = User.objects.update_or_create(username=username, defaults={
            "first_name": first, "last_name": last, "user_type": utype, "department": dept,
            "is_department_head": is_head, "expertise": expertise, "email": f"{username}@example.com",
            "is_active": True})
        if created:
            user.set_unusable_password()
            user.save()
        return user

    # ── اجرای همه ──
    def run(self, scenarios=None):
        scenarios = scenarios or SCENARIOS
        if self.live:
            return self._run_all(scenarios)
        # آفلاین: عامل‌ها خاموش (هیچ تماسی با n8n نیست). اگر داده نگه داشته نمی‌شود، فایل‌های
        # بارگذاری‌شده هم در پوشه‌ی موقت می‌روند تا پس از rollback فایل یتیمی در media نماند.
        with override_settings(AGENTS_ENABLED=False), ExitStack() as stack:
            if not self.persist:
                media = stack.enter_context(tempfile.TemporaryDirectory(prefix="e2e-media-"))
                stack.enter_context(override_settings(MEDIA_ROOT=media))
            return self._run_all(scenarios)

    def _run_all(self, scenarios):
        if self.persist:
            cleanup()
            return [self.run_one(s) for s in scenarios]
        results = []
        try:
            with transaction.atomic():
                results = [self.run_one(s) for s in scenarios]
                raise Rollback
        except Rollback:
            pass
        return results

    def run_one(self, scenario):
        result = ScenarioResult(scenario)
        people = self.cast(scenario)
        self.log(f"▶ {scenario.title}")
        case = None
        for step in scenario.steps:
            day = self._day(step, case)
            with mock.patch("django.utils.timezone.localdate", return_value=day):
                try:
                    case, step_result = self._step(step, day, case, people, scenario, result)
                except Exception as e:          # خطای برنامه: سناریو متوقف می‌شود
                    result.error = f"{step.action}: {type(e).__name__}: {e}"
                    self.log(f"   ✗ {result.error}")
                    break
            result.steps.append(step_result)
            self.log(f"   {'✓' if step_result.ok else '✗'} {step_result.action} "
                     f"({step_result.actor}) → {step_result.got}")
            if not step_result.ok:
                break                           # وضعیت از مسیر سناریو خارج شده؛ ادامه بی‌معناست
        if case is not None and self.live:
            self._observe_notifications(result)
        return result

    def _day(self, step, case):
        if callable(step.at):
            if case is None:
                raise ValueError("گام نسبی به پرونده پیش از ساخت پرونده آمده است")
            case.refresh_from_db()
            return step.at(case)
        return self.start + timedelta(days=step.at)

    def _resolve(self, value, people, case):
        if isinstance(value, str) and value.startswith("@"):
            return people[value]
        if isinstance(value, list):
            return [self._resolve(v, people, case) for v in value]
        if callable(value):
            return value(case)
        return value

    # ── یک گام ──
    def _step(self, step, day, case, people, scenario, result):
        actor = people[step.actor]
        payload = {k: self._resolve(v, people, case) for k, v in step.payload.items()}
        expected = (step.expect.label if isinstance(step.expect, State)
                    else step.expect or "انجام شود")
        res = StepResult(day, actor.username.removeprefix(PREFIX), step.action, expected, "", False,
                         note=step.note)

        if step.action == "create":
            try:
                case = wf.create_case(actor, TITLE_TAG + documents.TOPICS[scenario.topic].title_fa,
                                      documents.TOPICS[scenario.topic].title_en)
            except wf.WorkflowError as e:
                res.got = type(e).__name__
                res.detail = str(e)
            else:
                result.case_ids.append(case.pk)
                res.got = case.get_state_display()
        elif step.action == "upload_proposal":
            variant = payload.get("variant", "complete")
            version = wf.upload_document(case, actor, DocKind.PROPOSAL,
                                         documents.proposal_docx(scenario.topic, variant))
            res.got = f"نسخه‌ی {version.number} ({variant})"
            if self.live:
                self._observe_validation(result, version, variant)
        elif step.action == "upload_thesis":
            version = wf.upload_document(case, actor, DocKind.THESIS, documents.thesis_docx(scenario.topic))
            res.got = f"نسخه‌ی {version.number}"
        elif step.action == "advisor_suggest":
            res.got = "فقط در اجرای زنده"
            if self.live:
                run = agents.request_advisor_suggestions(case, payload["interests"])
                res.got = "درخواست شد"
                self._observe_suggestions(result, run, "پیشنهاد استاد راهنما", payload.get("expect_top"))
        elif step.action == "reminders":
            res.detail = check_reminders(case, day, payload["expect"])
            res.got = "مطابق قواعد" if not res.detail else "نامطابق"
            res.expected = describe_reminders(payload["expect"])
            res.ok = not res.detail
            return case, res
        else:
            note = payload.pop("note", "")
            try:
                case = wf.perform(case, step.action, actor, note=note, **payload)
            except wf.NotAllowed as e:
                res.got, res.detail = NOT_ALLOWED, str(e)
            except wf.GuardFailed as e:
                res.got, res.detail = GUARD, str(e)
            else:
                res.got = case.get_state_display()
                if self.live and case.state == State.REFEREE_ASSIGNMENT:
                    run = SuggestionRun.objects.filter(case=case, purpose="referee").first()
                    if run:
                        self._observe_suggestions(result, run, "پیشنهاد داور",
                                                  people.get(scenario.referee_expert))

        res.ok = res.got == expected or step.expect is None and not res.detail
        if res.ok and step.check is not None:
            case.refresh_from_db()
            problem = step.check(case)
            if problem:
                res.ok, res.detail = False, problem
        return case, res

    # ── انتظار برای عامل‌ها (فقط اجرای زنده) ──
    def _wait(self, fetch, done):
        started = time.monotonic()
        obj = fetch()
        while not done(obj) and time.monotonic() - started < self.agent_timeout:
            time.sleep(2)
            obj = fetch()
        return obj, time.monotonic() - started

    def _observe_validation(self, result, version, variant):
        self.log("     … منتظر عامل اعتبارسنجی سند")
        validation, seconds = self._wait(
            lambda: DocumentValidation.objects.filter(version=version).first(),
            lambda v: v is not None and v.status != DocumentValidation.Status.PENDING)
        expected = documents.expected_missing(variant)
        expected_text = "ناقص: " + "، ".join(sorted(expected)) if expected else "مطابق قالب"
        if validation is None or validation.is_pending:
            result.observations.append(Observation("اعتبارسنجی سند", expected_text, "پاسخی نرسید", None,
                                                   seconds))
            return
        if validation.status == DocumentValidation.Status.ERROR:
            result.observations.append(Observation("اعتبارسنجی سند", expected_text, "خطا", None, seconds,
                                                   validation.error))
            return
        missing = {i["key"] for i in validation.problems}
        got = "ناقص: " + "، ".join(sorted(missing)) if missing else "مطابق قالب"
        result.observations.append(Observation(
            "اعتبارسنجی سند", expected_text, got, missing == expected, seconds,
            f"تشخیص درست {len(expected & missing)} از {len(expected)} نقص؛ "
            f"{len(missing - expected)} هشدار نادرست"))

    def _observe_suggestions(self, result, run, label, expect_top):
        self.log(f"     … منتظر عامل {label}")
        run, seconds = self._wait(lambda: SuggestionRun.objects.get(pk=run.pk),
                                  lambda r: r.status != SuggestionRun.Status.PENDING)
        expected = expect_top.username.removeprefix(PREFIX) if expect_top else "—"
        if run.status != SuggestionRun.Status.DONE:
            result.observations.append(Observation(label, f"رتبه‌ی ۱: {expected}",
                                                   "پاسخی نرسید" if run.is_pending else "خطا",
                                                   None, seconds, run.error))
            return
        ranking = [s.referee.username.removeprefix(PREFIX) for s in run.suggestions.select_related("referee")]
        top = ranking[0] if ranking else "—"
        result.observations.append(Observation(
            label, f"رتبه‌ی ۱: {expected}", f"رتبه‌ی ۱: {top}", (top == expected) if expect_top else None,
            seconds, "ترتیب: " + "، ".join(ranking)))

    def _observe_notifications(self, result):
        self.log("     … منتظر ارسال اعلان‌ها")
        cases = ProjectCase.objects.filter(pk__in=result.case_ids)
        rows, seconds = self._wait(
            lambda: list(Notification.objects.filter(case__in=cases)),
            lambda ns: all(n.status != Notification.Status.QUEUED for n in ns))
        sent = [n for n in rows if n.status == Notification.Status.SENT]
        llm = sum(n.generated_by == "llm" for n in sent)
        result.observations.append(Observation(
            "اعلان ایمیلی", f"{len(rows)} اعلان ارسال شود", f"{len(sent)} ارسال شد",
            len(sent) == len(rows) if rows else None, seconds,
            f"متن مدل زبانی: {llm}، متن ثابت پشتیبان: {len(sent) - llm}، "
            f"ناموفق: {sum(n.status == Notification.Status.FAILED for n in rows)}"))


# ───────────────────────── یادآوری مهلت ─────────────────────────

def _reminder_rows(case, day):
    """{نوع مهلت: (مرحله، {نام کاربری گیرندگان})} برای یادآوری‌های سررسیدشده‌ی این پرونده در این روز."""
    kinds = dict(case.deadlines.values_list("pk", "kind"))
    rows = {}
    for item in agents.due_reminders(day):
        _, deadline_id, stage, user_id = item["key"].split(":")
        kind = kinds.get(int(deadline_id))
        if kind is None:
            continue
        username = User.objects.get(pk=int(user_id)).username.removeprefix(PREFIX)
        rows.setdefault(kind, (stage, set()))[1].add(username)
    return rows


def check_reminders(case, day, expect):
    """یادآوری‌های واقعی را با انتظار سناریو مقایسه می‌کند؛ خروجی: متن ناهمخوانی یا رشته‌ی خالی."""
    got = _reminder_rows(case, day)
    want = {kind: (stage, {_alias(u, case) for u in users}) for kind, (stage, users) in expect.items()}
    if got == want:
        return ""
    return f"سامانه: {describe_rows(got) or 'هیچ'} | انتظار: {describe_rows(want) or 'هیچ'}"


def _alias(token, case):
    if token == "@author":
        return case.author.username.removeprefix(PREFIX)
    return CAST[token][0]


def describe_rows(rows):
    return "؛ ".join(f"{kind}:{stage}→{','.join(sorted(users))}" for kind, (stage, users) in sorted(rows.items()))


def describe_reminders(expect):
    if not expect:
        return "بدون یادآوری"
    return "؛ ".join(f"{kind}:{stage}" for kind, (stage, _) in expect.items())


# ───────────────────────── پاک‌سازی ─────────────────────────

def scenario_cases():
    return ProjectCase.objects.filter(author__username__startswith=PREFIX)


def cleanup():
    """
    پرونده‌های سناریو (و فایل‌هایشان) را پاک و کاربران سناریو را غیرفعال می‌کند تا در فهرست
    استادان واجد شرایط برای دانشجویان واقعی دیده نشوند. اجرای بعدی دوباره فعالشان می‌کند.
    """
    cases = list(scenario_cases())
    for case in cases:
        for version in case.versions.all():
            version.file.delete(save=False)
    with transaction.atomic():
        # Review.version از نوع PROTECT است؛ پس اول رأی‌ها، بعد پرونده‌ها (و به‌تبع نسخه‌ها و رویدادها)
        Review.objects.filter(assignment__case__in=scenario_cases()).delete()
        deleted = scenario_cases().delete()[1].get("workflow.ProjectCase", 0)
        User.objects.filter(username__startswith=PREFIX).update(is_active=False)
    return deleted
