"""
موتور اجرای گردش‌کار — تنها جایی که وضعیت یک پرونده را تغییر می‌دهد.

statemachine.py می‌گوید «چه گذارهایی وجود دارد»؛ این فایل آن‌ها را «اجرا» می‌کند:
  ۱. پرونده را قفل می‌کند (select_for_update) تا دو اقدام هم‌زمان با هم تداخل نکنند
  ۲. بررسی می‌کند اقدام در وضعیت فعلی مجاز است و کاربر نقش لازم را در همین پرونده دارد
  ۳. پیش‌شرط‌ها (guards) را بررسی و اثرات جانبی (effects) را اعمال می‌کند
  ۴. وضعیت جدید را ذخیره، مهلت‌ها را به‌روز و یک CaseEvent ثبت می‌کند
همه‌ی این مراحل در یک تراکنش‌اند: یا همه انجام می‌شود یا هیچ‌کدام.

ویوها (views.py) هیچ‌گاه مستقیم case.state را تغییر نمی‌دهند؛ فقط perform() را صدا می‌زنند.
در فاز ۳ هم n8n از همین توابع و همین CaseEventها استفاده می‌کند.
"""
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from . import rules
from . import statemachine as sm
from .enums import DeadlineKind, DocKind, ReviewDecision, State, UserType
from .jalali import fa_digits, to_jalali
from .models import (CaseEvent, Deadline, DocumentVersion, ProjectCase, RefereeAssignment,
                     Review, User)


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
    if user.is_superuser or user.user_type == UserType.EDU_STAFF:
        return qs
    mine = Q(author=user) | Q(advisor=user) | Q(referee_assignments__referee=user)
    if user.is_department_head and user.department_id:
        mine |= Q(department_id=user.department_id)
    return qs.filter(mine).distinct()


def latest_version(case, kind):
    return case.versions.filter(kind=kind).order_by("-number").first()


def eligible_advisors():
    return (User.objects.filter(user_type=UserType.FACULTY, department__isnull=False, is_active=True)
            .select_related("department").order_by("last_name", "first_name"))


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
    """یک اقدام قابل‌نمایش به کاربر. اگر blocked پر باشد دکمه غیرفعال و دلیلش نمایش داده می‌شود."""
    transition: sm.Transition
    blocked: str = ""


def blocker(case, action, user):
    """پیش‌شرط‌هایی که به ورودی فرم نیاز ندارند. خروجی: متن دلیل، یا رشته‌ی خالی اگر مانعی نیست."""
    if action == "submit_proposal":
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
        offers.append(Offer(t, reason))
    return offers


def is_pending_for(case, user):
    """آیا پرونده «در انتظار اقدام» این کاربر است؟ (لغو همیشه ممکن است و اقدام در انتظار حساب نمی‌شود)"""
    return any(o.transition.action != "cancel" for o in available_actions(case, user))


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
    c.case.advisor = advisor
    c.case.department = advisor.department
    c.data["advisor_id"] = advisor.pk


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
    return State.REVISION if needs_revision else State.GROUP_APPROVAL


def _group_approve(c):
    c.data["late"] = _satisfy(c.case, DeadlineKind.GROUP_REVIEW, c.today)


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
    "edu_register": _edu_register,
    "assign_referees": _assign_referees,
    "submit_review": _submit_review,
    "group_approve": _group_approve,
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
    """تنها نقطه‌ی ثبت رویداد؛ در فاز ۳ فراخوانی Webhook عامل‌های n8n همین‌جا اضافه می‌شود."""
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
