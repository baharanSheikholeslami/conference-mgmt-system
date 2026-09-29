"""
تعریف «جدولی» ماشین‌حالت پرونده — تنها منبع حقیقت قواعد گذار.

این فایل عمداً فقط داده است، نه منطق اجرا:
  • چه کسی (actor = نقش زمینه‌ای) در کدام وضعیت‌ها (sources) کدام اقدام را می‌تواند انجام دهد
  • اقدام به کدام وضعیت‌ها (targets) می‌رود
  • پیش‌شرط‌ها (guards) و اثرات جانبی (effects) به‌صورت متن، برای مستندسازی
اجرای واقعی گذارها (تراکنش، بررسی پیش‌شرط‌ها، ثبت CaseEvent) در فاز ۲ نوشته می‌شود.
اصل انسان‌در‌حلقه: هیچ عامل هوش مصنوعی actor نیست؛ همه‌ی گذارها اقدام انسان‌اند.
"""
from dataclasses import dataclass

from .enums import CaseRole as Role
from .enums import State as S


@dataclass(frozen=True)
class Transition:
    action: str
    label: str
    actor: str
    sources: tuple
    targets: tuple
    note_required: bool = False
    guards: tuple = ()
    effects: tuple = ()


INITIAL_STATE = S.ADVISOR_SELECTION
TERMINAL_STATES = (S.COMPLETED, S.REJECTED, S.CANCELLED)
ACTIVE_STATES = tuple(s for s in S if s not in TERMINAL_STATES)

_T = Transition
TRANSITIONS = {t.action: t for t in [
    _T("select_advisor", "انتخاب استاد راهنما", Role.AUTHOR,
       (S.ADVISOR_SELECTION,), (S.DRAFTING,),
       guards=("استاد انتخاب‌شده عضو هیئت‌علمی و عضو یک گروه آموزشی باشد",),
       effects=("advisor و department پرونده مقداردهی می‌شوند",)),
    _T("submit_proposal", "ارسال پیشنهاد برای استاد راهنما", Role.AUTHOR,
       (S.DRAFTING,), (S.ADVISOR_REVIEW,),
       guards=("حداقل یک نسخه‌ی «پیشنهاد» بارگذاری شده باشد",)),
    _T("advisor_return", "بازگرداندن پیشنهاد به دانشجو", Role.ADVISOR,
       (S.ADVISOR_REVIEW,), (S.DRAFTING,), note_required=True),
    _T("advisor_approve", "تأیید پیشنهاد و امضای استاد راهنما", Role.ADVISOR,
       (S.ADVISOR_REVIEW,), (S.EDU_REGISTRATION,)),
    _T("edu_register", "ثبت دریافت پیشنهاد در آموزش دانشکده", Role.EDU,
       (S.EDU_REGISTRATION,), (S.REFEREE_ASSIGNMENT,),
       effects=("edu_received_at = امروز (شروع مهلت ۲ماهه‌ی داوری گروه)",
                "اگر پس از مهلت تحویل پیشنهاد باشد، رویداد با data.late=true ثبت می‌شود (مسدودکننده نیست)")),
    _T("assign_referees", "تخصیص داور(ان) توسط مدیر گروه", Role.HEAD,
       (S.REFEREE_ASSIGNMENT,), (S.REFEREE_REVIEW,),
       guards=("تعداد داوران ≥ حداقل لازم (۱ برای دانشجو، ۲ برای نویسنده غیردانشجو)",
               "هر داور عضو هیئت‌علمی همان گروه و غیر از استاد راهنما و نویسنده باشد")),
    _T("submit_review", "ثبت رأی داور (تأیید / بازگشت جهت اصلاح)", Role.REFEREE,
       (S.REFEREE_REVIEW,), (S.REFEREE_REVIEW, S.REVISION, S.GROUP_APPROVAL),
       guards=("داور در فهرست داوران پرونده باشد", "هر داور برای هر نسخه فقط یک رأی دارد",
               "رأی «اصلاح» نیازمند نظر مکتوب است"),
       effects=("تا وقتی همه‌ی داوران برای نسخه‌ی جاری رأی نداده‌اند، وضعیت تغییر نمی‌کند",
                "پس از رأی همه: اگر حتی یک «اصلاح» → REVISION، وگرنه → GROUP_APPROVAL")),
    _T("resubmit_proposal", "ارسال نسخه‌ی اصلاح‌شده برای داوری", Role.AUTHOR,
       (S.REVISION,), (S.REFEREE_REVIEW,),
       guards=("نسخه‌ای جدیدتر از آخرین دور داوری بارگذاری شده باشد",)),
    _T("group_approve", "تصویب پیشنهاد در گروه", Role.HEAD,
       (S.GROUP_APPROVAL,), (S.COUNCIL_APPROVAL,),
       effects=("مهلت GROUP_REVIEW برآورده می‌شود",)),
    _T("group_reject", "رد پیشنهاد در گروه", Role.HEAD,
       (S.GROUP_APPROVAL,), (S.REJECTED,), note_required=True),
    _T("council_approve", "تصویب در شورای دانشکده و ثبت نهایی", Role.EDU,
       (S.COUNCIL_APPROVAL,), (S.IN_PROGRESS,),
       effects=("final_approved_at = امروز", "مهلت DEFENSE_EARLIEST (تصویب + ۳ ماه) ساخته می‌شود")),
    _T("council_reject", "رد پیشنهاد در شورای دانشکده", Role.EDU,
       (S.COUNCIL_APPROVAL,), (S.REJECTED,), note_required=True),
    _T("submit_thesis", "ارسال پایان‌نامه برای تأیید استاد راهنما", Role.AUTHOR,
       (S.IN_PROGRESS,), (S.THESIS_ADVISOR_REVIEW,),
       guards=("حداقل یک نسخه‌ی «پایان‌نامه» بارگذاری شده باشد",)),
    _T("thesis_return", "بازگرداندن پایان‌نامه به دانشجو", Role.ADVISOR,
       (S.THESIS_ADVISOR_REVIEW,), (S.IN_PROGRESS,), note_required=True),
    _T("thesis_approve", "تأیید پایان‌نامه و تحویل به آموزش و داور(ان)", Role.ADVISOR,
       (S.THESIS_ADVISOR_REVIEW,), (S.THESIS_SUBMITTED,),
       effects=("thesis_submitted_at = امروز",
                "اگر پس از مهلت THESIS_DELIVERY باشد، رویداد با data.late=true ثبت می‌شود")),
    _T("schedule_defense", "تعیین زمان و مکان دفاع", Role.EDU,
       (S.THESIS_SUBMITTED,), (S.DEFENSE_SCHEDULED,),
       guards=("defense_date ≥ final_approved_at + ۳ ماه (توضیح ۴)", "محل دفاع الزامی است")),
    _T("record_result", "ثبت نتیجه‌ی دفاع و نمره", Role.ADVISOR,
       (S.DEFENSE_SCHEDULED,), (S.COMPLETED,),
       guards=("نویسنده دانشجو: نمره‌ی بین ۰ تا ۲۰ الزامی است",
               "نویسنده غیردانشجو: نمره ثبت نمی‌شود (مشمول نمره‌دهی نیست)")),
    _T("cancel", "لغو پرونده", Role.EDU,
       ACTIVE_STATES, (S.CANCELLED,), note_required=True),
]}


def transitions_from(state):
    return [t for t in TRANSITIONS.values() if state in t.sources]


def reachable_states(start=INITIAL_STATE):
    seen, stack = {start}, [start]
    while stack:
        for t in transitions_from(stack.pop()):
            for target in t.targets:
                if target not in seen:
                    seen.add(target)
                    stack.append(target)
    return seen


def to_mermaid(skip=("cancel",)):
    lines = ["stateDiagram-v2", "    direction TB"]
    for s in S:
        lines.append(f'    state "{s.label}" as {s.name}')
    lines.append(f"    [*] --> {INITIAL_STATE.name}")
    for t in TRANSITIONS.values():
        if t.action in skip:
            continue
        for src in t.sources:
            for dst in t.targets:
                lines.append(f"    {src.name} --> {dst.name}: {t.action}")
    for s in TERMINAL_STATES:
        lines.append(f"    {s.name} --> [*]")
    return "\n".join(lines)


def to_markdown_table():
    rows = ["| # | اقدام | عنوان | نقش | از وضعیت | به وضعیت | توضیح الزامی؟ | پیش‌شرط‌ها | اثرات |",
            "|---|---|---|---|---|---|---|---|---|"]
    for i, t in enumerate(TRANSITIONS.values(), 1):
        src = "همه‌ی وضعیت‌های فعال" if t.sources == ACTIVE_STATES else "، ".join(s.name for s in t.sources)
        dst = "، ".join(s.name for s in t.targets)
        rows.append(f"| {i} | `{t.action}` | {t.label} | {t.actor.label} | {src} | {dst} | "
                    f"{'بله' if t.note_required else '—'} | {'؛ '.join(t.guards) or '—'} | "
                    f"{'؛ '.join(t.effects) or '—'} |")
    return "\n".join(rows)
