"""
موتور اجرای گردش‌کار — تنها جایی که وضعیت یک پرونده را تغییر می‌دهد.

statemachine.py می‌گوید «چه گذارهایی وجود دارد»؛ این فایل آن‌ها را «اجرا» می‌کند:
  ۱. پرونده را قفل می‌کند (select_for_update) تا دو اقدام هم‌زمان با هم تداخل نکنند
  ۲. بررسی می‌کند اقدام در وضعیت فعلی مجاز است و کاربر نقش لازم را در همین پرونده دارد
  ۳. پیش‌شرط‌ها (guards) را بررسی و اثرات جانبی (effects) را اعمال می‌کند
  ۴. وضعیت جدید را ذخیره، مهلت‌ها را به‌روز و یک CaseEvent ثبت می‌کند
همه‌ی این مراحل در یک تراکنش‌اند: یا همه انجام می‌شود یا هیچ‌کدام.

ویوها (views.py) هیچ‌گاه مستقیم case.state را تغییر نمی‌دهند؛ فقط perform() را صدا می‌زنند.
عامل‌های n8n (فاز ۳) فقط به CaseEventها گوش می‌دهند و هیچ‌وقت perform() را صدا نمی‌زنند.
"""
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Max, Q
from django.utils import timezone

from . import rules
from . import statemachine as sm
from .enums import CaseRole, DeadlineKind, DocKind, ReviewDecision, State, UserType
from .jalali import fa_digits, to_jalali
from .models import (CaseEvent, Deadline, DocumentComment, DocumentVersion, ProjectCase,
                     RefereeAssignment, Review, Signature, User)


# ───────────────────────── خطاها ─────────────────────────

class WorkflowError(Exception):
    """اقدام انجام نشد؛ متن خطا فارسی و قابل‌نمایش به کاربر است."""


class NotAllowed(WorkflowError):
    """کاربر نقش لازم را ندارد یا اقدام در این وضعیت تعریف نشده است."""


class GuardFailed(WorkflowError):
    """نقش و وضعیت درست است، اما یکی از پیش‌شرط‌ها برقرار نیست."""


# ───────────────────────── پرس‌وجوهای کمکی ─────────────────────────

def cases_for(user):
    """پرونده‌هایی که کاربر حق دیدنشان را دارد (پایه‌ی کنترل دسترسی همه‌ی صفحه‌ها)."""
    qs = (ProjectCase.objects.select_related("author", "advisor", "department")
          .prefetch_related("referee_assignments", "deadlines"))
    if sees_all_projects(user):
        return qs
    mine = Q(author=user) | Q(advisor=user) | Q(referee_assignments__referee=user)
    if user.is_department_head and user.department_id:
        mine |= Q(department_id=user.department_id)
    return qs.filter(mine).distinct()


def sees_all_projects(user):
    """آموزش دانشکده (و مدیر سامانه) فهرست همه‌ی پروژه‌ها را می‌بیند."""
    return user.is_superuser or user.user_type == UserType.EDU_STAFF


def latest_version(case, kind):
    return case.versions.filter(kind=kind).order_by("-number").first()


# پرونده‌هایی که راهنمایشان هنوز استاد را «مشغول» نکرده‌اند (درخواست رد یا بی‌پاسخ، یا پروژه تمام شده)
_NOT_COUNTED_FOR_ADVISOR = (State.ADVISOR_SELECTION, State.ADVISOR_PENDING, State.REJECTED,
                            State.CANCELLED, State.COMPLETED)


def advisee_count(advisor):
    """تعداد دانشجویانی که استاد اکنون راهنمایشان است (درخواست‌های پذیرفته‌شده و پروژه‌های جاری)."""
    return ProjectCase.objects.filter(advisor=advisor).exclude(state__in=_NOT_COUNTED_FOR_ADVISOR).count()


def advisor_has_room(advisor):
    return advisee_count(advisor) < rules.MAX_ADVISEES


def eligible_advisors():
    """استادانی که دانشجو می‌تواند از آن‌ها درخواست راهنمایی کند: ظرفیتشان پر نشده باشد."""
    faculty = (User.objects.filter(user_type=UserType.FACULTY, department__isnull=False, is_active=True)
               .select_related("department").order_by("last_name", "first_name"))
    return faculty.exclude(pk__in=[u.pk for u in faculty if not advisor_has_room(u)])


def eligible_referees(case):
    """اعضای هیئت‌علمی همان گروه، به‌جز استاد راهنما و نویسنده."""
    return (User.objects.filter(user_type=UserType.FACULTY, department_id=case.department_id,
                                is_active=True)
            .exclude(pk__in=[case.advisor_id, case.author_id])
            .order_by("last_name", "first_name"))


def has_reviewed(case, user, version):
    return Review.objects.filter(assignment__case=case, assignment__referee=user,
                                 version=version).exists()


def next_deadline(case):
    """نزدیک‌ترین مهلتِ هنوز انجام‌نشده (برای نمایش در داشبورد). از کش prefetch استفاده می‌کند."""
    if case.is_terminal:
        return None
    open_deadlines = [d for d in case.deadlines.all()
                      if d.satisfied_at is None and not d.is_lower_bound]
    return min(open_deadlines, key=lambda d: d.due_date, default=None)


# ───────────────────────── اقدام‌های در دسترس ─────────────────────────

@dataclass(frozen=True)
class Offer:
    """
    یک اقدام قابل‌نمایش به کاربر. اگر blocked پر باشد دکمه غیرفعال و دلیلش نمایش داده می‌شود.
    waiting یعنی مانع فقط با گذشت زمان رفع می‌شود (مثل نرسیدن روز دفاع) و کاری از کاربر ساخته نیست.
    """
    transition: sm.Transition
    blocked: str = ""
    waiting: bool = False

    @property
    def pending(self):
        """آیا این اقدام پرونده را «در انتظار اقدام» کاربر می‌کند؟ (لغو همیشه ممکن است و حساب نمی‌شود)"""
        return self.transition.action != "cancel" and not self.waiting


def defense_is_ahead(case):
    return bool(case.defense_date) and timezone.localdate() < case.defense_date


def blocker(case, action, user):
    """پیش‌شرط‌هایی که به ورودی فرم نیاز ندارند. خروجی: متن دلیل، یا رشته‌ی خالی اگر مانعی نیست."""
    if action == "advisor_accept":
        if not advisor_has_room(case.advisor):
            return (f"ظرفیت راهنمایی شما ({fa_digits(rules.MAX_ADVISEES)} دانشجو) پر است؛ "
                    "این درخواست را رد کنید تا دانشجو استاد دیگری انتخاب کند.")
    elif action == "submit_proposal":
        if not latest_version(case, DocKind.PROPOSAL):
            return "ابتدا فایل پیشنهاد را بارگذاری کنید."
    elif action == "resubmit_proposal":
        version = latest_version(case, DocKind.PROPOSAL)
        if version is None or version.reviews.exists():
            return "ابتدا نسخه‌ی اصلاح‌شده‌ی پیشنهاد را بارگذاری کنید."
    elif action == "submit_thesis":
        if not latest_version(case, DocKind.THESIS):
            return "ابتدا فایل پایان‌نامه را بارگذاری کنید."
    elif action == "assign_referees":
        if eligible_referees(case).count() < case.required_referees:
            return "در این گروه آموزشی داور واجد شرایط به تعداد کافی ثبت نشده است."
    elif action == "submit_review":
        version = latest_version(case, DocKind.PROPOSAL)
        if version is None:
            return "هنوز نسخه‌ای از پیشنهاد برای داوری بارگذاری نشده است."
        if has_reviewed(case, user, version):
            return "شما برای این نسخه قبلاً رأی داده‌اید."
    elif action == "record_result":
        if defense_is_ahead(case):
            return f"ثبت نتیجه پیش از روز دفاع ({to_jalali(case.defense_date)}) ممکن نیست."
    return ""


def available_actions(case, user):
    """اقدام‌هایی که این کاربر، با نقش‌هایش در همین پرونده، در وضعیت فعلی می‌تواند انجام دهد."""
    roles = case.roles_of(user)
    offers = []
    for t in sm.transitions_from(case.state):
        if t.actor not in roles:
            continue
        reason = blocker(case, t.action, user)
        if t.action == "submit_review" and reason:
            continue          # داوری که رأی داده دیگر کاری در این پرونده ندارد
        waiting = t.action == "record_result" and defense_is_ahead(case)
        offers.append(Offer(t, reason, waiting))
    return offers


def is_pending_for(case, user):
    """آیا پرونده «در انتظار اقدام» این کاربر است؟"""
    return any(o.pending for o in available_actions(case, user))


# ───────────────────────── نظر بازگشت، دسترسی به نسخه‌ها ─────────────────────────

# گذارهایی که پرونده را با یک نظر مکتوب به دانشجو برمی‌گردانند
_RETURN_TITLES = {
    "advisor_return": "استاد راهنما پیشنهاد را برای اصلاح برگرداند",
    "thesis_return": "استاد راهنما پایان‌نامه را برای اصلاح برگرداند",
    "advisor_decline": "استاد راهنما درخواست راهنمایی شما را نپذیرفت؛ استاد دیگری انتخاب کنید",
    "submit_review": "داوران پیشنهاد را برای اصلاح برگرداندند",
}
_STUDENT_STATES = (State.ADVISOR_SELECTION, State.DRAFTING, State.REVISION, State.IN_PROGRESS)


def pending_feedback(case):
    """
    نظر ثبت‌شده‌ای که دانشجو باید پیش از ادامه بخواند، یا None.
    فقط وقتی برمی‌گردد که آخرین تغییر وضعیتِ پرونده همان «بازگشت» باشد؛ پس با ارسال دوباره ناپدید می‌شود.
    """
    if case.state not in _STUDENT_STATES:
        return None
    last = case.events.exclude(from_state=F("to_state")).select_related("actor").last()
    if last is None or last.to_state != case.state or last.action not in _RETURN_TITLES:
        return None
    if last.action == "submit_review":
        version = latest_version(case, DocKind.PROPOSAL)
        reviews = Review.objects.filter(version=version, decision=ReviewDecision.REVISE)
        items = [{"who": str(r.assignment.referee), "note": r.comment}
                 for r in reviews.select_related("assignment__referee")]
    else:
        items = [{"who": str(last.actor or "سامانه"), "note": last.note}]
    return {"title": _RETURN_TITLES[last.action], "items": items, "at": last.created_at}


def sees_all_versions(case, user):
    """نسخه‌های پیشین سند را فقط نویسنده، استاد راهنما و آموزش دانشکده می‌بینند."""
    return user.is_superuser or bool(case.roles_of(user) & {CaseRole.AUTHOR, CaseRole.ADVISOR, CaseRole.EDU})


def visible_versions(case, user):
    versions = case.versions.select_related("uploaded_by").prefetch_related("reviews__assignment__referee")
    if sees_all_versions(case, user):
        return versions.order_by("kind", "-number")
    latest = [latest_version(case, kind).pk for kind in DocKind.values if latest_version(case, kind)]
    return versions.filter(pk__in=latest).order_by("kind", "-number")


def visible_events(case, user):
    """تاریخچه‌ی پرونده؛ بارگذاری نسخه‌های پیشین فقط برای کسانی که حق دیدن آن نسخه‌ها را دارند."""
    events = case.events.select_related("actor")
    if sees_all_versions(case, user):
        return events
    latest = {kind: v.number for kind in DocKind.values if (v := latest_version(case, kind))}
    return [e for e in events
            if not e.action.startswith("upload_")
            or e.data.get("version") == latest.get(e.action.removeprefix("upload_"))]


def can_download(case, user, version):
    return sees_all_versions(case, user) or version.pk == getattr(latest_version(case, version.kind), "pk", None)


# ───────────────────────── اثرات هر گذار ─────────────────────────

@dataclass
class _Ctx:
    case: ProjectCase
    actor: User
    today: object
    note: str
    payload: dict
    data: dict = field(default_factory=dict)     # در CaseEvent.data ذخیره می‌شود


def _satisfy(case, kind, today):
    """مهلت را «انجام‌شده» علامت می‌زند. خروجی: آیا پس از موعد انجام شد؟"""
    due = case.expected_deadlines().get(kind)
    if due is None:
        return False
    Deadline.objects.update_or_create(case=case, kind=kind,
                                      defaults={"due_date": due, "satisfied_at": today})
    return today > due


def _validated(obj):
    """قواعد clean() مدل را اجرا می‌کند و خطای آن را به GuardFailed تبدیل می‌کند."""
    try:
        obj.full_clean()
    except ValidationError as e:
        raise GuardFailed(" ".join(e.messages)) from e
    return obj


def _select_advisor(c):
    advisor = c.payload.get("advisor")
    if (advisor is None or advisor.user_type != UserType.FACULTY or not advisor.department_id
            or advisor.pk == c.case.author_id):
        raise GuardFailed("استاد راهنما باید عضو هیئت‌علمی و عضو یک گروه آموزشی باشد.")
    if not advisor_has_room(advisor):
        raise GuardFailed(f"ظرفیت راهنمایی {advisor} پر است ({fa_digits(rules.MAX_ADVISEES)} دانشجو)؛ "
                          "استاد دیگری را انتخاب کنید.")
    c.case.advisor = advisor
    c.case.department = advisor.department
    c.data["advisor_id"] = advisor.pk


def _advisor_decline(c):
    c.data["advisor_id"] = c.case.advisor_id
    c.case.advisor = None
    c.case.department = None


def _edu_register(c):
    c.case.edu_received_at = c.today
    c.data["late"] = _satisfy(c.case, DeadlineKind.PROPOSAL_SUBMISSION, c.today)


def _assign_referees(c):
    referees = list({r.pk: r for r in (c.payload.get("referees") or [])}.values())
    if len(referees) < c.case.required_referees:
        raise GuardFailed(f"برای این پرونده حداقل {fa_digits(c.case.required_referees)} داور لازم است.")
    c.case.referee_assignments.exclude(referee__in=referees).delete()
    already = set(c.case.referee_assignments.values_list("referee_id", flat=True))
    for referee in referees:
        if referee.pk not in already:
            _validated(RefereeAssignment(case=c.case, referee=referee, assigned_by=c.actor)).save()
    c.data["referee_ids"] = sorted(r.pk for r in referees)


def _submit_review(c):
    assignment = c.case.referee_assignments.get(referee=c.actor)
    version = latest_version(c.case, DocKind.PROPOSAL)
    review = _validated(Review(assignment=assignment, version=version,
                               decision=c.payload.get("decision") or "", comment=c.note))
    review.save()
    c.data.update(decision=review.decision, version=version.number)

    # تا وقتی همه‌ی داوران برای نسخه‌ی جاری رأی نداده‌اند، وضعیت تغییر نمی‌کند
    if c.case.referee_assignments.exclude(reviews__version=version).exists():
        return State.REFEREE_REVIEW
    needs_revision = Review.objects.filter(assignment__case=c.case, version=version,
                                           decision=ReviewDecision.REVISE).exists()
    if needs_revision:
        return State.REVISION
    c.data["late"] = _satisfy(c.case, DeadlineKind.GROUP_REVIEW, c.today)   # داوری تمام شد
    return State.COUNCIL_APPROVAL


def _council_approve(c):
    c.case.final_approved_at = c.today      # مهلت DEFENSE_EARLIEST را sync_deadlines می‌سازد


def _thesis_approve(c):
    c.case.thesis_submitted_at = c.today
    c.data["late"] = _satisfy(c.case, DeadlineKind.THESIS_DELIVERY, c.today)


def _schedule_defense(c):
    defense_date = c.payload.get("defense_date")
    place = (c.payload.get("defense_place") or "").strip()
    if not defense_date or not place:
        raise GuardFailed("تاریخ و محل دفاع هر دو الزامی‌اند.")
    earliest = rules.add_months(c.case.final_approved_at, rules.MIN_MONTHS_APPROVAL_TO_DEFENSE)
    if defense_date < earliest:
        raise GuardFailed(f"دفاع باید حداقل {fa_digits(rules.MIN_MONTHS_APPROVAL_TO_DEFENSE)} ماه پس از "
                          f"تصویب نهایی باشد؛ زودترین تاریخ مجاز {to_jalali(earliest)} است.")
    c.case.defense_date = defense_date
    c.case.defense_place = place
    c.data.update(defense_date=defense_date.isoformat(), defense_place=place)


def _record_result(c):
    grade = c.payload.get("grade")
    if not c.case.is_graded:
        if grade is not None:
            raise GuardFailed("نویسنده غیردانشجو مشمول نمره‌دهی نیست.")
        return
    try:
        grade = Decimal(str(grade))
    except InvalidOperation:
        raise GuardFailed("ثبت نمره الزامی است.") from None
    if not 0 <= grade <= 20:
        raise GuardFailed("نمره باید بین ۰ تا ۲۰ باشد.")
    c.case.grade = grade
    c.data["grade"] = str(grade)
    c.data["late"] = _satisfy(c.case, DeadlineKind.GRADE_ENTRY, c.today)


# گذارهایی که این‌جا نیستند (مثل advisor_approve) هیچ اثر جانبی جز تغییر وضعیت ندارند.
_EFFECTS = {
    "select_advisor": _select_advisor,
    "advisor_decline": _advisor_decline,
    "edu_register": _edu_register,
    "assign_referees": _assign_referees,
    "submit_review": _submit_review,
    "council_approve": _council_approve,
    "thesis_approve": _thesis_approve,
    "schedule_defense": _schedule_defense,
    "record_result": _record_result,
}


# ───────────────────────── اجرای گذار ─────────────────────────

def _locked(case):
    return ProjectCase.objects.select_for_update().get(pk=case.pk)


@transaction.atomic
def perform(case, action, actor, note="", **payload):
    """
    یک گذار ماشین‌حالت را اجرا می‌کند و پرونده‌ی به‌روزشده را برمی‌گرداند.
    در صورت مجاز نبودن NotAllowed و در صورت برقرار نبودن پیش‌شرط‌ها GuardFailed می‌دهد؛
    در هر دو حالت هیچ تغییری در پایگاه‌داده نمی‌ماند.
    """
    t = sm.TRANSITIONS.get(action)
    if t is None:
        raise NotAllowed("اقدام ناشناخته است.")
    case = _locked(case)
    if case.state not in t.sources:
        raise NotAllowed(f"اقدام «{t.label}» در وضعیت «{case.get_state_display()}» ممکن نیست.")
    if t.actor not in case.roles_of(actor):
        raise NotAllowed(f"اقدام «{t.label}» فقط توسط «{t.actor.label}» این پرونده ممکن است.")
    note = (note or "").strip()
    if t.note_required and not note:
        raise GuardFailed("برای این اقدام نوشتن توضیح الزامی است.")
    reason = blocker(case, action, actor)
    if reason:
        raise GuardFailed(reason)

    ctx = _Ctx(case=case, actor=actor, today=timezone.localdate(), note=note, payload=payload)
    from_state = case.state
    effect = _EFFECTS.get(action)
    target = (effect(ctx) if effect else None) or t.targets[0]
    if target not in t.targets:        # خطای برنامه‌نویسی، نه خطای کاربر
        raise RuntimeError(f"{action}: مقصد {target} در ماشین‌حالت تعریف نشده است")

    case.state = target
    case.save()
    case.sync_deadlines()
    _log(case, actor, action, from_state, note, ctx.data)
    return case


def _log(case, actor, action, from_state, note="", data=None):
    """
    تنها نقطه‌ی ثبت رویداد. عامل‌های n8n (فاز ۳) به ثبت همین رویدادها گوش می‌دهند
    (agents/signals.py)؛ این فایل عمداً از وجود آن‌ها بی‌خبر است.
    """
    return CaseEvent.objects.create(case=case, actor=actor, action=action, from_state=from_state,
                                    to_state=case.state, note=note, data=data or {})


# ───────────────────────── عملیات بدون تغییر وضعیت ─────────────────────────

AUTHOR_TYPES = (UserType.STUDENT, UserType.EXTERNAL)
EDITABLE_STATES = (State.ADVISOR_SELECTION, State.DRAFTING, State.REVISION)
UPLOAD_STATES = {
    DocKind.PROPOSAL: (State.DRAFTING, State.REVISION),
    DocKind.THESIS: (State.IN_PROGRESS,),
}
EXTRA_ACTION_LABELS = {
    "create_case": "ثبت پرونده",
    "edit_case": "ویرایش عنوان پرونده",
    "upload_proposal": "بارگذاری نسخه‌ی پیشنهاد",
    "upload_thesis": "بارگذاری نسخه‌ی پایان‌نامه",
    # اقدام‌های حذف‌شده از گردش‌کار؛ فقط برای نمایش تاریخچه‌ی پرونده‌های قدیمی
    "group_approve": "تصویب پیشنهاد در گروه (مرحله‌ی حذف‌شده)",
    "group_reject": "رد پیشنهاد در گروه (مرحله‌ی حذف‌شده)",
}


def action_label(action):
    t = sm.TRANSITIONS.get(action)
    return t.label if t else EXTRA_ACTION_LABELS.get(action, action)


def can_create_case(user):
    return user.user_type in AUTHOR_TYPES


def can_edit(case, user):
    return user.id == case.author_id and case.state in EDITABLE_STATES


def uploadable_kind(case, user):
    """نوع سندی که نویسنده در وضعیت فعلی می‌تواند بارگذاری کند (یا None)."""
    if user.id != case.author_id:
        return None
    return next((kind for kind, states in UPLOAD_STATES.items() if case.state in states), None)


@transaction.atomic
def create_case(author, title_fa, title_en="", academic_year=None, semester=None):
    if not can_create_case(author):
        raise NotAllowed("فقط دانشجو یا نویسنده غیردانشجو می‌تواند پرونده ثبت کند.")
    today = timezone.localdate()
    default_year, default_semester = rules.current_term(today)
    academic_year = academic_year or default_year
    semester = semester or default_semester
    same_term = ProjectCase.objects.filter(author=author, academic_year=academic_year, semester=semester)
    if same_term.exclude(state__in=[State.REJECTED, State.CANCELLED]).exists():
        raise GuardFailed("شما در این نیم‌سال یک پرونده‌ی فعال دارید.")
    case = ProjectCase.objects.create(author=author, title_fa=title_fa, title_en=title_en,
                                      academic_year=academic_year, semester=semester,
                                      registration_date=today)
    case.sync_deadlines()
    _log(case, author, "create_case", case.state)
    return case


@transaction.atomic
def edit_case(case, user, title_fa, title_en=""):
    case = _locked(case)
    if not can_edit(case, user):
        raise NotAllowed("عنوان پرونده در این وضعیت قابل‌ویرایش نیست.")
    old = {"title_fa": case.title_fa, "title_en": case.title_en}
    case.title_fa, case.title_en = title_fa, title_en
    case.save()
    _log(case, user, "edit_case", case.state, data={"old": old})
    return case


@transaction.atomic
def upload_document(case, user, kind, file, note=""):
    case = _locked(case)
    if uploadable_kind(case, user) != kind:
        raise NotAllowed("بارگذاری این سند در وضعیت فعلی پرونده ممکن نیست.")
    last = case.versions.filter(kind=kind).aggregate(n=Max("number"))["n"] or 0
    version = DocumentVersion.objects.create(case=case, kind=kind, number=last + 1, file=file,
                                             note=note, uploaded_by=user)
    _log(case, user, f"upload_{kind}", case.state, note,
         {"version": version.number, "version_id": version.pk})
    return version


# ───────────────────────── بررسی سند در نمایشگر: نظر روی بخش‌ها، تأیید با امضا، بازگرداندن ─────────────────────────

@dataclass(frozen=True)
class DocDecision:
    """دو اقدامی که استاد در نمایشگر یک سند دارد: تأیید (با امضای الکترونیک) و بازگرداندن برای اصلاح."""
    role: str
    approve: str
    revise: str
    review: bool = False          # داور: هر دو اقدام همان submit_review با رأی متفاوت است

    @property
    def approve_label(self):
        return "تأیید و ثبت امضای الکترونیک"

    @property
    def revise_label(self):
        return "بازگرداندن برای اصلاح"


# (نقش، وضعیت، نوع سند) ← اقدام‌های تأیید و بازگرداندن
_DOC_DECISIONS = {
    (CaseRole.ADVISOR, State.ADVISOR_REVIEW, DocKind.PROPOSAL): ("advisor_approve", "advisor_return"),
    (CaseRole.ADVISOR, State.THESIS_ADVISOR_REVIEW, DocKind.THESIS): ("thesis_approve", "thesis_return"),
    (CaseRole.REFEREE, State.REFEREE_REVIEW, DocKind.PROPOSAL): ("submit_review", "submit_review"),
}
SIGNATURE_EXTENSIONS = ("png", "jpg", "jpeg")
MAX_SIGNATURE_MB = 2


def doc_decision(case, user, version):
    """اقدام تأیید/بازگرداندنی که این کاربر همین حالا روی این نسخه دارد، یا None."""
    latest = latest_version(case, version.kind)
    if latest is None or latest.pk != version.pk:
        return None
    for role in case.roles_of(user):
        actions = _DOC_DECISIONS.get((role, case.state, version.kind))
        if actions is None:
            continue
        offers = {o.transition.action: o for o in available_actions(case, user)}
        if all(a in offers and not offers[a].blocked for a in actions):
            return DocDecision(role, *actions, review=actions[0] == "submit_review")
    return None


def can_comment(case, user, version):
    """استاد راهنما و داوران تا پایان پرونده روی آخرین نسخه‌ی سند نظر می‌گذارند."""
    if case.is_terminal or not case.roles_of(user) & {CaseRole.ADVISOR, CaseRole.REFEREE}:
        return False
    latest = latest_version(case, version.kind)
    return latest is not None and latest.pk == version.pk


def add_comment(case, user, version, text, section=0, section_title=""):
    text = (text or "").strip()
    if not can_comment(case, user, version):
        raise NotAllowed("شما نمی‌توانید روی این نسخه نظر بگذارید.")
    if not text:
        raise GuardFailed("متن نظر را بنویسید.")
    return DocumentComment.objects.create(version=version, author=user, section=section,
                                          section_title=section_title[:120], text=text)


def _comments_note(version, user):
    """اگر استاد هنگام بازگرداندن توضیحی ننوشت، نظرهایش روی بخش‌ها همان توضیح است."""
    lines = []
    for c in version.comments.filter(author=user):
        lines.append(f"• {c.section_title}: {c.text}" if c.section_title else f"• {c.text}")
    return "\n".join(lines)


def _check_signature(image):
    if image is None:
        raise GuardFailed("برای تأیید، تصویر امضای خود را بارگذاری کنید.")
    extension = image.name.rsplit(".", 1)[-1].lower() if "." in image.name else ""
    if extension not in SIGNATURE_EXTENSIONS:
        raise GuardFailed("تصویر امضا باید PNG یا JPG باشد.")
    if image.size > MAX_SIGNATURE_MB * 1024 * 1024:
        raise GuardFailed(f"حجم تصویر امضا نباید بیشتر از {fa_digits(MAX_SIGNATURE_MB)} مگابایت باشد.")


@transaction.atomic
def decide_document(case, user, version, approve, note="", signature=None):
    """
    تأیید یا بازگرداندن یک سند از نمایشگر. تأیید فقط با تصویر امضا انجام می‌شود: امضا ذخیره و
    روی محل امضای سند قرار می‌گیرد (signing.py). خروجی: پرونده‌ی به‌روزشده.
    """
    decision = doc_decision(case, user, version)
    if decision is None:
        raise NotAllowed("در وضعیت فعلی تصمیمی درباره‌ی این سند با شما نیست.")
    note = (note or "").strip()
    if approve:
        _check_signature(signature)
        action = decision.approve
    else:
        note = note or _comments_note(version, user)
        if not note:
            raise GuardFailed("برای بازگرداندن، توضیح بنویسید یا روی بخش‌های سند نظر بگذارید.")
        action = decision.revise
    payload = {}
    if decision.review:
        payload["decision"] = ReviewDecision.APPROVE if approve else ReviewDecision.REVISE
    case = perform(case, action, user, note=note, **payload)
    if approve:
        from .signing import sign_version            # Pillow/reportlab فقط این‌جا لازم است
        record = Signature.objects.create(version=version, signer=user, role=decision.role, image=signature)
        sign_version(record)
    return case
