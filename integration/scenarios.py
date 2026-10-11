"""
سناریوهای مصنوعی سرتاسری (فاز ۴) — «از انتخاب استاد راهنما تا ثبت نمره».

این فایل فقط داده است (مثل workflow/statemachine.py): هر سناریو فهرستی از گام‌هاست و هر گام
می‌گوید «در کدام روز، چه کسی، چه اقدامی، با چه ورودی» و «نتیجه‌ی مورد انتظار چیست».
اجرای گام‌ها (ساعت شبیه‌سازی‌شده، کاربران، انتظار برای عامل‌ها و گزارش) در runner.py است.

قرارداد نام‌ها:
  • "@author"، "@advisor"، "@ref_ai"، … به بازیگران سناریو اشاره می‌کنند (runner.CAST)
  • expect یک وضعیت (State)، یا "NotAllowed" / "GuardFailed" برای اقدامی که باید رد شود
  • at روز گام است: عدد = چند روز پس از شروع سناریو؛ تابع = تاریخی که از روی پرونده حساب می‌شود
"""
from dataclasses import dataclass, field

from workflow import rules
from workflow.enums import DeadlineKind as K
from workflow.enums import ReviewDecision, State

APPROVE, REVISE = ReviewDecision.APPROVE, ReviewDecision.REVISE
NOT_ALLOWED, GUARD = "NotAllowed", "GuardFailed"


@dataclass(frozen=True)
class Step:
    at: object                      # int (روز نسبی) یا callable(case) -> date
    actor: str                      # "@author" و …
    action: str                     # گذار ماشین‌حالت، یا یکی از SPECIAL_ACTIONS
    payload: dict = field(default_factory=dict)
    expect: object = None           # State، NOT_ALLOWED، GUARD، یا None (گام بدون گذار)
    check: object = None            # callable(case) -> متن خطا یا "" ؛ بررسی اضافه پس از گام
    note: str = ""                  # توضیح گام در گزارش


# گام‌هایی که گذار ماشین‌حالت نیستند
SPECIAL_ACTIONS = ("create", "upload_proposal", "upload_thesis", "advisor_suggest", "reminders")


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    author_type: str                # "student" یا "external"
    topic: str                      # کلید documents.TOPICS
    steps: tuple
    referee_expert: str = ""        # استادی که انتظار داریم عامل پیشنهاد داور اول بیاورد (فقط اجرای زنده)
    description: str = ""


# ───────────────────────── تاریخ‌های وابسته به پرونده ─────────────────────────

def defense_day(case):
    return case.defense_date


def earliest_defense(extra_days=0):
    def at(case):
        from datetime import timedelta
        return (rules.add_months(case.final_approved_at, rules.MIN_MONTHS_APPROVAL_TO_DEFENSE)
                + timedelta(days=extra_days))
    return at


def deadline_day(kind, offset=0):
    """روزی نسبت به یک مهلت پرونده (برای آزمودن یادآوری‌ها)."""
    def at(case):
        from datetime import timedelta
        return case.expected_deadlines()[kind] + timedelta(days=offset)
    return at


# ───────────────────────── بررسی‌های اضافه ─────────────────────────

def all_deadlines_met(case):
    open_ = [d.get_kind_display() for d in case.deadlines.all()
             if d.satisfied_at is None and not d.is_lower_bound]
    return f"مهلت‌های انجام‌نشده: {'، '.join(open_)}" if open_ else ""


def graded(value):
    def check(case):
        return "" if case.grade is not None and float(case.grade) == value else f"نمره {case.grade} است"
    return check


def not_graded(case):
    if case.grade is not None:
        return "نویسنده غیردانشجو نباید نمره داشته باشد"
    if case.deadlines.filter(kind=K.GRADE_ENTRY).exists():
        return "نویسنده غیردانشجو نباید مهلت درج نمره داشته باشد"
    return ""


def last_event_late(expected):
    def check(case):
        late = case.events.last().data.get("late")
        return "" if bool(late) == expected else f"late={late} اما انتظار {expected} بود"
    return check


def counts(versions=None, reviews=None):
    def check(case):
        from workflow.models import Review
        n_versions = case.versions.filter(kind="proposal").count()
        n_reviews = Review.objects.filter(assignment__case=case).count()
        if versions is not None and n_versions != versions:
            return f"{n_versions} نسخه‌ی پیشنهاد (انتظار {versions})"
        if reviews is not None and n_reviews != reviews:
            return f"{n_reviews} رأی داوری (انتظار {reviews})"
        return ""
    return check


INTERESTS_AI = ("می‌خواهم روی مدل‌های زبانی بزرگ، پردازش زبان طبیعی فارسی و عامل‌های هوشمند برای "
                "پاسخ‌گویی خودکار کار کنم.")


# ───────────────────────── سناریوها ─────────────────────────

S = Step
SCENARIOS = [
    Scenario(
        "happy_path", "مسیر کامل دانشجو بدون بازگشت", "student", "llm_agents",
        referee_expert="@ref_ai",
        description="از انتخاب استاد راهنما (با پیشنهاد سامانه) تا ثبت نمره؛ همه‌ی مهلت‌ها به‌موقع.",
        steps=(
            S(0, "@author", "create", expect=State.ADVISOR_SELECTION),
            S(0, "@author", "advisor_suggest", {"interests": INTERESTS_AI, "expect_top": "@advisor"}),
            S(0, "@author", "select_advisor", {"advisor": "@advisor"}, State.DRAFTING),
            S(2, "@author", "upload_proposal", {"variant": "complete"}),
            S(2, "@author", "submit_proposal", expect=State.ADVISOR_REVIEW),
            S(4, "@advisor", "advisor_approve", expect=State.EDU_REGISTRATION),
            S(6, "@edu", "edu_register", expect=State.REFEREE_ASSIGNMENT, check=last_event_late(False)),
            S(8, "@head", "assign_referees", {"referees": ["@ref_ai"]}, State.REFEREE_REVIEW),
            S(20, "@ref_ai", "submit_review", {"decision": APPROVE}, State.GROUP_APPROVAL),
            S(25, "@head", "group_approve", expect=State.COUNCIL_APPROVAL, check=last_event_late(False)),
            S(30, "@edu", "council_approve", expect=State.IN_PROGRESS),
            S(100, "@author", "upload_thesis"),
            S(100, "@author", "submit_thesis", expect=State.THESIS_ADVISOR_REVIEW),
            S(103, "@advisor", "thesis_approve", expect=State.THESIS_SUBMITTED, check=last_event_late(False)),
            S(105, "@edu", "schedule_defense",
              {"defense_date": earliest_defense(7), "defense_place": "تالار دانشکده"}, State.DEFENSE_SCHEDULED),
            S(defense_day, "@advisor", "record_result", {"grade": 18.5}, State.COMPLETED,
              check=lambda c: graded(18.5)(c) or all_deadlines_met(c)),
        )),
    Scenario(
        "revision_loop", "دو بار بازگشت: استاد راهنما و داور", "student", "databases",
        referee_expert="@ref_db",
        description="نسخه‌ی اول ناقص است (بدون «روش ارزیابی»)، استاد راهنما برمی‌گرداند، داور اصلاح می‌خواهد.",
        steps=(
            S(0, "@author", "create", expect=State.ADVISOR_SELECTION),
            S(0, "@author", "select_advisor", {"advisor": "@advisor"}, State.DRAFTING),
            S(3, "@author", "upload_proposal", {"variant": "no_evaluation"}),
            S(3, "@author", "submit_proposal", expect=State.ADVISOR_REVIEW),
            S(5, "@advisor", "advisor_return", {"note": "بخش روش ارزیابی را اضافه کنید."}, State.DRAFTING),
            S(8, "@author", "upload_proposal", {"variant": "complete"}),
            S(8, "@author", "submit_proposal", expect=State.ADVISOR_REVIEW),
            S(9, "@advisor", "advisor_approve", expect=State.EDU_REGISTRATION),
            S(10, "@edu", "edu_register", expect=State.REFEREE_ASSIGNMENT),
            S(12, "@head", "assign_referees", {"referees": ["@ref_db"]}, State.REFEREE_REVIEW),
            S(30, "@ref_db", "submit_review", {"decision": REVISE, "note": "معیار ارزیابی را کمّی کنید."},
              State.REVISION),
            S(31, "@author", "resubmit_proposal", expect=GUARD, note="بدون نسخه‌ی تازه رد می‌شود"),
            S(35, "@author", "upload_proposal", {"variant": "complete"}),
            S(35, "@author", "resubmit_proposal", expect=State.REFEREE_REVIEW),
            S(40, "@ref_db", "submit_review", {"decision": APPROVE}, State.GROUP_APPROVAL,
              check=counts(versions=3, reviews=2)),
            S(42, "@head", "group_approve", expect=State.COUNCIL_APPROVAL),
            S(45, "@edu", "council_approve", expect=State.IN_PROGRESS),
        )),
    Scenario(
        "non_student", "نویسنده غیردانشجو: دو داور و بدون نمره", "external", "llm_agents",
        description="استثنای آیین‌نامه: حداقل دو داور لازم است و نویسنده مشمول نمره‌دهی نیست.",
        steps=(
            S(0, "@author", "create", expect=State.ADVISOR_SELECTION),
            S(0, "@author", "select_advisor", {"advisor": "@advisor"}, State.DRAFTING),
            S(1, "@author", "upload_proposal", {"variant": "complete"}),
            S(1, "@author", "submit_proposal", expect=State.ADVISOR_REVIEW),
            S(2, "@advisor", "advisor_approve", expect=State.EDU_REGISTRATION),
            S(3, "@edu", "edu_register", expect=State.REFEREE_ASSIGNMENT),
            S(4, "@head", "assign_referees", {"referees": ["@ref_ai"]}, GUARD, note="یک داور کافی نیست"),
            S(4, "@head", "assign_referees", {"referees": ["@ref_ai", "@ref_db"]}, State.REFEREE_REVIEW),
            S(10, "@ref_ai", "submit_review", {"decision": APPROVE}, State.REFEREE_REVIEW,
              note="تا رأی داور دوم، وضعیت تغییر نمی‌کند"),
            S(12, "@ref_db", "submit_review", {"decision": APPROVE}, State.GROUP_APPROVAL),
            S(14, "@head", "group_approve", expect=State.COUNCIL_APPROVAL),
            S(15, "@edu", "council_approve", expect=State.IN_PROGRESS),
            S(90, "@author", "upload_thesis"),
            S(90, "@author", "submit_thesis", expect=State.THESIS_ADVISOR_REVIEW),
            S(92, "@advisor", "thesis_approve", expect=State.THESIS_SUBMITTED),
            S(93, "@edu", "schedule_defense",
              {"defense_date": earliest_defense(3), "defense_place": "کلاس ۲۰۱"}, State.DEFENSE_SCHEDULED),
            S(defense_day, "@advisor", "record_result", {"grade": 17}, GUARD, note="نمره ثبت نمی‌شود"),
            S(defense_day, "@advisor", "record_result", expect=State.COMPLETED, check=not_graded),
        )),
    Scenario(
        "group_reject", "رد پیشنهاد در گروه و ثبت دوباره", "student", "databases",
        description="پس از رد در گروه، دانشجو می‌تواند در همان نیم‌سال پرونده‌ی تازه ثبت کند.",
        steps=(
            S(0, "@author", "create", expect=State.ADVISOR_SELECTION),
            S(0, "@author", "select_advisor", {"advisor": "@advisor"}, State.DRAFTING),
            S(1, "@author", "upload_proposal", {"variant": "complete"}),
            S(1, "@author", "submit_proposal", expect=State.ADVISOR_REVIEW),
            S(2, "@advisor", "advisor_approve", expect=State.EDU_REGISTRATION),
            S(3, "@edu", "edu_register", expect=State.REFEREE_ASSIGNMENT),
            S(4, "@head", "assign_referees", {"referees": ["@ref_net"]}, State.REFEREE_REVIEW),
            S(15, "@ref_net", "submit_review", {"decision": APPROVE}, State.GROUP_APPROVAL),
            S(20, "@head", "group_reject", expect=GUARD, note="رد بدون توضیح ممکن نیست"),
            S(20, "@head", "group_reject", {"note": "موضوع با برنامه‌ی گروه هم‌خوان نیست."}, State.REJECTED),
            S(21, "@author", "create", expect=State.ADVISOR_SELECTION, note="پرونده‌ی تازه در همان نیم‌سال"),
        )),
    Scenario(
        "late_and_reminders", "تأخیر در تحویل پیشنهاد و یادآوری مهلت", "student", "llm_agents",
        description="یادآوری‌ها دقیقاً در ۱۴، ۷ و ۰ روز مانده و پس از مهلت صادر می‌شوند؛ تحویل دیرهنگام ثبت می‌شود.",
        steps=(
            S(0, "@author", "create", expect=State.ADVISOR_SELECTION),
            S(0, "@author", "select_advisor", {"advisor": "@advisor"}, State.DRAFTING),
            S(deadline_day(K.PROPOSAL_SUBMISSION, -20), "@author", "reminders", {"expect": {}},
              note="۲۰ روز مانده: هنوز زود است"),
            S(deadline_day(K.PROPOSAL_SUBMISSION, -14), "@author", "reminders",
              {"expect": {K.PROPOSAL_SUBMISSION: ("d14", ["@author"])}}),
            S(deadline_day(K.PROPOSAL_SUBMISSION, -7), "@author", "reminders",
              {"expect": {K.PROPOSAL_SUBMISSION: ("d7", ["@author"])}}),
            S(deadline_day(K.PROPOSAL_SUBMISSION, 0), "@author", "reminders",
              {"expect": {K.PROPOSAL_SUBMISSION: ("d0", ["@author"])}}),
            S(deadline_day(K.PROPOSAL_SUBMISSION, 3), "@author", "reminders",
              {"expect": {K.PROPOSAL_SUBMISSION: ("overdue1", ["@author"])}}),
            S(deadline_day(K.PROPOSAL_SUBMISSION, 4), "@author", "upload_proposal", {"variant": "complete"}),
            S(deadline_day(K.PROPOSAL_SUBMISSION, 4), "@author", "submit_proposal", expect=State.ADVISOR_REVIEW),
            S(deadline_day(K.PROPOSAL_SUBMISSION, 9), "@advisor", "reminders",
              {"expect": {K.PROPOSAL_SUBMISSION: ("overdue2", ["@author", "@advisor"])}},
              note="اکنون نوبت استاد راهنماست؛ او هم یادآوری می‌گیرد"),
            S(deadline_day(K.PROPOSAL_SUBMISSION, 10), "@advisor", "advisor_approve",
              expect=State.EDU_REGISTRATION),
            S(deadline_day(K.PROPOSAL_SUBMISSION, 12), "@edu", "edu_register",
              expect=State.REFEREE_ASSIGNMENT, check=last_event_late(True), note="ثبت دیرهنگام"),
            S(deadline_day(K.GROUP_REVIEW, -7), "@head", "reminders",
              {"expect": {K.GROUP_REVIEW: ("d7", ["@author", "@head"])}},
              note="مهلت تحویل پیشنهاد انجام شده و دیگر یادآوری ندارد؛ نوبت مهلت داوری گروه است"),
        )),
    Scenario(
        "guards", "نگهبان‌ها و کنترل دسترسی", "student", "databases",
        description="هر اقدام غیرمجاز یا بدون پیش‌شرط باید رد شود و وضعیت پرونده دست‌نخورده بماند.",
        steps=(
            S(0, "@author", "create", expect=State.ADVISOR_SELECTION),
            S(0, "@edu", "select_advisor", {"advisor": "@advisor"}, NOT_ALLOWED, note="فقط خود دانشجو"),
            S(0, "@author", "select_advisor", {"advisor": "@edu"}, GUARD, note="کارشناس آموزش استاد نیست"),
            S(0, "@author", "select_advisor", {"advisor": "@advisor"}, State.DRAFTING),
            S(1, "@author", "submit_proposal", expect=GUARD, note="بدون فایل پیشنهاد"),
            S(1, "@author", "upload_proposal", {"variant": "complete"}),
            S(1, "@author", "submit_proposal", expect=State.ADVISOR_REVIEW),
            S(2, "@ref_db", "advisor_approve", expect=NOT_ALLOWED, note="داور، استاد راهنمای این پرونده نیست"),
            S(2, "@advisor", "advisor_return", expect=GUARD, note="بازگرداندن بدون توضیح"),
            S(2, "@advisor", "advisor_approve", expect=State.EDU_REGISTRATION),
            S(3, "@edu", "edu_register", expect=State.REFEREE_ASSIGNMENT),
            S(4, "@head", "assign_referees", {"referees": ["@advisor"]}, GUARD, note="راهنما داور نمی‌شود"),
            S(4, "@head", "assign_referees", {"referees": ["@ref_db"]}, State.REFEREE_REVIEW),
            S(9, "@ref_net", "submit_review", {"decision": APPROVE}, NOT_ALLOWED, note="داور این پرونده نیست"),
            S(9, "@ref_db", "submit_review", {"decision": REVISE}, GUARD, note="«اصلاح» بدون نظر مکتوب"),
            S(9, "@ref_db", "submit_review", {"decision": APPROVE}, State.GROUP_APPROVAL),
            S(10, "@edu", "group_approve", expect=NOT_ALLOWED, note="فقط مدیر گروه"),
            S(10, "@head", "group_approve", expect=State.COUNCIL_APPROVAL),
            S(11, "@edu", "council_approve", expect=State.IN_PROGRESS),
            S(80, "@author", "submit_thesis", expect=GUARD, note="بدون فایل پایان‌نامه"),
            S(80, "@author", "upload_thesis"),
            S(80, "@author", "submit_thesis", expect=State.THESIS_ADVISOR_REVIEW),
            S(81, "@advisor", "thesis_approve", expect=State.THESIS_SUBMITTED),
            S(82, "@edu", "schedule_defense", {"defense_date": earliest_defense(-1), "defense_place": "کلاس ۱"},
              GUARD, note="کمتر از سه ماه پس از تصویب"),
            S(82, "@edu", "schedule_defense", {"defense_date": earliest_defense(0), "defense_place": "کلاس ۱"},
              State.DEFENSE_SCHEDULED),
            S(83, "@advisor", "record_result", {"grade": 19}, GUARD, note="پیش از روز دفاع"),
            S(defense_day, "@advisor", "record_result", {"grade": 25}, GUARD, note="نمره‌ی بیش از ۲۰"),
            S(defense_day, "@author", "record_result", {"grade": 20}, NOT_ALLOWED, note="دانشجو نمره نمی‌دهد"),
            S(defense_day, "@advisor", "record_result", {"grade": 19}, State.COMPLETED, check=graded(19)),
        )),
    Scenario(
        "cancel", "لغو پرونده توسط آموزش", "student", "llm_agents",
        description="لغو فقط با توضیح و فقط توسط آموزش دانشکده؛ پس از لغو هیچ اقدامی ممکن نیست.",
        steps=(
            S(0, "@author", "create", expect=State.ADVISOR_SELECTION),
            S(0, "@author", "select_advisor", {"advisor": "@advisor"}, State.DRAFTING),
            S(5, "@author", "cancel", {"note": "انصراف"}, NOT_ALLOWED, note="دانشجو نمی‌تواند لغو کند"),
            S(5, "@edu", "cancel", expect=GUARD, note="لغو بدون توضیح"),
            S(5, "@edu", "cancel", {"note": "انصراف دانشجو از پروژه"}, State.CANCELLED),
            S(6, "@author", "submit_proposal", expect=NOT_ALLOWED, note="پرونده‌ی بسته"),
        )),
]

BY_KEY = {s.key: s for s in SCENARIOS}
