"""
ویوهای پورتال. هر ویو سه کار می‌کند و نه بیشتر:
  ۱. پرونده را فقط از میان پرونده‌های قابل‌مشاهده‌ی کاربر برمی‌دارد (services.cases_for)
  ۲. فرم را نمایش می‌دهد / اعتبارسنجی می‌کند
  ۳. کار اصلی را به services می‌سپارد و خطای آن را روی فرم نشان می‌دهد
"""
from dataclasses import dataclass

from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.contrib.messages.views import SuccessMessageMixin
from django.core.exceptions import PermissionDenied
from django.db.models import F, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_GET, require_POST

from agents import services as agents

from . import forms, rules, services, viewer
from . import statemachine as sm
from .enums import CaseRole, DeadlineKind, DocKind, State, UserType
from .models import Deadline, DocumentVersion, ProjectCase, Signature

# ترتیب مسیر اصلی، برای نوار پیشرفت صفحه‌ی پرونده («اصلاح» هم‌ردیف «داوری» است)
STAGES = [State.ADVISOR_SELECTION, State.ADVISOR_PENDING, State.DRAFTING, State.ADVISOR_REVIEW,
          State.EDU_REGISTRATION, State.REFEREE_ASSIGNMENT, State.REFEREE_REVIEW,
          State.COUNCIL_APPROVAL, State.IN_PROGRESS, State.THESIS_ADVISOR_REVIEW,
          State.THESIS_SUBMITTED, State.DEFENSE_SCHEDULED, State.COMPLETED]
STAGE_OF = {state: i for i, state in enumerate(STAGES, 1)} | {
    State.REVISION: STAGES.index(State.REFEREE_REVIEW) + 1}

# پنج «مرحله»ی قابل‌فهم برای دانشجو و استاد؛ هر مرحله چند وضعیت ماشین‌حالت را در بر می‌گیرد
PHASES = [
    ("انتخاب استاد راهنما", (State.ADVISOR_SELECTION, State.ADVISOR_PENDING)),
    ("پیشنهاد پروژه", (State.DRAFTING, State.ADVISOR_REVIEW)),
    ("داوری و تصویب", (State.EDU_REGISTRATION, State.REFEREE_ASSIGNMENT, State.REFEREE_REVIEW,
                       State.REVISION, State.COUNCIL_APPROVAL)),
    ("اجرا و پایان‌نامه", (State.IN_PROGRESS, State.THESIS_ADVISOR_REVIEW)),
    ("دفاع", (State.THESIS_SUBMITTED, State.DEFENSE_SCHEDULED)),
]
PHASE_OF = {state: i for i, (_, states) in enumerate(PHASES) for state in states}


def phases(case):
    """گام‌های نوار مرحله‌ها: done / current / next. پرونده‌ی ردشده یا لغوشده نوار ندارد."""
    if case.state in (State.REJECTED, State.CANCELLED):
        return []
    current = len(PHASES) if case.state == State.COMPLETED else PHASE_OF[case.state]
    return [{"label": label, "status": "done" if i < current else "current" if i == current else "next"}
            for i, (label, _) in enumerate(PHASES)]


SUGGESTION_PURPOSE = {"select_advisor": "advisor", "assign_referees": "referee"}


def _posted(request):
    """داده‌ی فرم ارسال‌شده، یا None برای نمایش اولیه‌ی فرم (GET)."""
    return request.POST if request.method == "POST" else None


def _case_or_404(user, pk):
    """پرونده‌ای که کاربر حق دیدنش را ندارد برای او «وجود ندارد» (۴۰۴، نه ۴۰۳)."""
    return get_object_or_404(services.cases_for(user), pk=pk)


@dataclass
class Row:
    """یک سطر داشبورد."""
    case: object
    roles: list
    offers: list
    deadline: object
    role_set: frozenset = frozenset()

    @property
    def todo(self):
        return [o for o in self.offers if o.pending]


class PasswordChange(SuccessMessageMixin, auth_views.PasswordChangeView):
    """کاربر رمز اولیه‌ای را که ادمین داده عوض می‌کند (ویوی آماده‌ی جنگو + قالب فرم خودمان)."""
    template_name = "workflow/form.html"
    success_url = reverse_lazy("dashboard")
    success_message = "رمز عبور شما تغییر کرد."
    extra_context = {"title": "تغییر رمز عبور", "submit_label": "ذخیره‌ی رمز جدید"}


def _waiting_days(case):
    """چند روز است که پرونده در وضعیت فعلی منتظر مانده (از آخرین تغییر وضعیت)."""
    last = case.events.exclude(from_state=F("to_state")).last()
    since = timezone.localtime(last.created_at).date() if last else case.registration_date
    return max((timezone.localdate() - since).days, 0)


def _reminders(rows):
    """کارت یادآور استاد راهنما و داور: چیزهایی که منتظر بررسی و تأیید اوست."""
    reviewing = {CaseRole.ADVISOR, CaseRole.REFEREE}
    return [{"case": r.case, "what": [o.transition.label for o in r.todo],
             "as": [role.label for role in sorted(r.role_set & reviewing)],
             "days": _waiting_days(r.case), "deadline": r.deadline}
            for r in rows if r.todo and r.role_set & reviewing]


STUDENT_DEADLINES = (DeadlineKind.PROPOSAL_SUBMISSION, DeadlineKind.THESIS_DELIVERY)


def _timers(rows, user):
    """
    یادآور مهلت‌های مهم دانشجو: فقط تحویل پیشنهاد و پایان‌نامه (دفاع جدا نمایش داده می‌شود)؛
    مهلت‌های داوری و نمره کار استادان و آموزش است و این‌جا نمی‌آید.
    """
    timers = []
    for r in rows:
        if CaseRole.AUTHOR not in r.role_set or r.case.is_terminal:
            continue
        for d in r.case.deadlines.all():
            if d.satisfied_at is None and d.kind in STUDENT_DEADLINES:
                timers.append({"case": r.case, "deadline": d, "key": d.due_date})
    return sorted(timers, key=lambda t: t["key"])


def _defense(case):
    """کارت دفاع دانشجو: تاریخ و محل اگر تعیین شده، وگرنه زودترین تاریخ مجاز پس از تصویب."""
    if case is None or case.is_terminal:
        return None
    if case.defense_date:
        return {"date": case.defense_date, "place": case.defense_place, "scheduled": True}
    earliest = next((d for d in case.deadlines.all() if d.kind == DeadlineKind.DEFENSE_EARLIEST), None)
    return {"date": earliest.due_date, "scheduled": False} if earliest else None


def _student_context(user, rows):
    mine = [r.case for r in rows if CaseRole.AUTHOR in r.role_set]
    case = next((c for c in mine if not c.is_terminal), mine[0] if mine else None)
    return {
        "case": case,
        "case_phases": phases(case) if case else [],
        "past": [c for c in mine if c != case],
        "choose_advisor": case is not None and case.state == State.ADVISOR_SELECTION,
        "defense": _defense(case),
    }


def _faculty_context(user, rows):
    head_rows = [r for r in rows if CaseRole.HEAD in r.role_set]
    return {
        "head_rows": head_rows,
        "head_pending": [r for r in head_rows if any(o.transition.actor == CaseRole.HEAD for o in r.todo)],
        "head_active": sum(1 for r in head_rows if not r.case.is_terminal),
    }


def _edu_context(rows):
    cases = ProjectCase.objects.all()
    counts = {s: 0 for s in State}
    for s in cases.values_list("state", flat=True):
        counts[s] += 1
    phase_rows = []
    for label, states in PHASES:
        items = [(st, counts[st]) for st in states]
        phase_rows.append({"label": label, "count": sum(n for _, n in items),
                           "states": [(st, n) for st, n in items if n]})
    top = max([p["count"] for p in phase_rows] + [1])
    for p in phase_rows:
        p["width"] = round(100 * p["count"] / top)
    today = timezone.localdate()
    overdue = (Deadline.objects.filter(satisfied_at__isnull=True, due_date__lt=today)
               .exclude(kind=DeadlineKind.DEFENSE_EARLIEST)
               .exclude(case__state__in=sm.TERMINAL_STATES)
               .select_related("case__author").order_by("due_date")[:8])
    return {
        "phase_rows": phase_rows,
        "overdue": overdue,
        "totals": {"active": sum(counts[s] for s in sm.ACTIVE_STATES), "completed": counts[State.COMPLETED],
                   "stopped": counts[State.REJECTED] + counts[State.CANCELLED]},
    }


@login_required
def dashboard(request):
    user = request.user
    rows = [Row(case=c, roles=[r.label for r in sorted(c.roles_of(user))],
                offers=services.available_actions(c, user), deadline=services.next_deadline(c),
                role_set=frozenset(c.roles_of(user)))
            for c in services.cases_for(user)]
    faculty = user.user_type == UserType.FACULTY
    is_edu = services.sees_all_projects(user)
    context = {
        "pending": [r for r in rows if r.todo],
        "others": [r for r in rows if not r.todo],
        "can_create": services.can_create_case(user),
        "reminders": _reminders(rows) if faculty else [],
        "timers": _timers(rows, user),
        "faculty": faculty,
        "advisees": [r for r in rows if CaseRole.ADVISOR in r.role_set and r.case.state not in (
            State.ADVISOR_SELECTION, State.REJECTED, State.CANCELLED)] if faculty else [],
        "refereeing": [r for r in rows if CaseRole.REFEREE in r.role_set] if faculty else [],
        "capacity": {"used": services.advisee_count(user), "max": rules.MAX_ADVISEES} if faculty else None,
        "is_edu": is_edu,
    }
    if is_edu:
        template = "workflow/dashboard_edu.html"
        context.update(_edu_context(rows))
    elif faculty:
        template = "workflow/dashboard_faculty.html"
        context.update(_faculty_context(user, rows))
    else:
        template = "workflow/dashboard_student.html"
        context.update(_student_context(user, rows))
    return render(request, template, context)


@login_required
def case_create(request):
    if not services.can_create_case(request.user):
        raise PermissionDenied
    year, semester = rules.current_term(timezone.localdate())
    form = forms.CaseCreateForm(_posted(request),
                                initial={"academic_year": year, "semester": semester})
    if request.method == "POST" and form.is_valid():
        try:
            case = services.create_case(request.user, **form.cleaned_data)
        except services.WorkflowError as e:
            form.add_error(None, str(e))
        else:
            messages.success(request, "پرونده ثبت شد. اکنون استاد راهنما را انتخاب کنید.")
            return redirect("case_detail", pk=case.pk)
    return render(request, "workflow/form.html", {
        "form": form, "title": "ثبت پرونده‌ی جدید", "submit_label": "ثبت پرونده"})


# اقدام‌هایی که دانشجو در باکس همان سند می‌بیند (نه در کارت «اقدام‌های شما»)
SUBMIT_ACTIONS = {"submit_proposal": DocKind.PROPOSAL, "resubmit_proposal": DocKind.PROPOSAL,
                  "submit_thesis": DocKind.THESIS}
# تأیید و بازگرداندن سند در نمایشگر کناری سند انجام می‌شود (تأیید همراه با امضای الکترونیک)
DOC_ACTIONS = {"advisor_approve": DocKind.PROPOSAL, "advisor_return": DocKind.PROPOSAL,
               "thesis_approve": DocKind.THESIS, "thesis_return": DocKind.THESIS,
               "submit_review": DocKind.PROPOSAL}
SIGN_ACTIONS = ("advisor_approve", "thesis_approve")
TEMPLATES = {DocKind.PROPOSAL: "workflow/templates/proposal-template.docx",
             DocKind.THESIS: "workflow/templates/thesis-template.docx"}


def _panel_url(case, version, mode=""):
    url = f"{reverse('case_detail', args=[case.pk])}?doc={version.pk}"
    return f"{url}&panel={mode}" if mode else url


def _action_buttons(case, offers):
    """
    کارت «اقدام‌های شما». ارسال سند (دانشجو) و تأیید/بازگرداندن سند (استاد) در باکس همان سند و
    نمایشگر آن است، پس این‌جا تکرار نمی‌شود؛ فقط اگر سندی نباشد پیوند به فرم معمولی می‌ماند.
    """
    buttons = []
    for o in offers:
        action = o.transition.action
        if action in SUBMIT_ACTIONS or action == "cancel":
            continue
        if action in DOC_ACTIONS and services.latest_version(case, DOC_ACTIONS[action]):
            continue
        buttons.append({"transition": o.transition, "blocked": o.blocked,
                        "url": reverse("case_action", args=[case.pk, action])})
    return buttons


def _doc_boxes(case, user, offers, versions):
    """دو باکس کنار هم «پیشنهاد» و «پایان‌نامه» با آخرین نسخه، نسخه‌های پیشین و اقدام دانشجو."""
    upload_kind = services.uploadable_kind(case, user)
    boxes = []
    for kind in (DocKind.PROPOSAL, DocKind.THESIS):
        mine = [v for v in versions if v.kind == kind]
        latest = mine[0] if mine else None
        submit = next((o for o in offers if SUBMIT_ACTIONS.get(o.transition.action) == kind), None)
        boxes.append({
            "kind": kind, "label": DocKind(kind).label, "latest": latest, "older": mine[1:],
            "template": TEMPLATES[kind], "can_upload": upload_kind == kind, "submit": submit,
            "is_author": user.id == case.author_id,
            "signatures": list(latest.signatures.select_related("signer")) if latest else [],
            "comment_count": latest.comments.count() if latest else 0,
            "decision": services.doc_decision(case, user, latest) if latest else None,
            "url": _panel_url(case, latest) if latest else "",
        })
    return boxes


def _panel(request, case, versions):
    """داده‌ی نمایشگر کناری سند (?doc=<نسخه>)، یا None."""
    try:
        pk = int(request.GET.get("doc", ""))
    except ValueError:
        return None
    version = next((v for v in versions if v.pk == pk), None)
    if version is None:
        return None
    user = request.user
    parts = viewer.sections(version)
    comments = list(version.comments.select_related("author"))
    general = viewer.with_comments(parts, comments)
    decision = services.doc_decision(case, user, version)
    mode = request.GET.get("panel", "")
    is_pdf = version.file.name.lower().endswith(".pdf")
    return {
        "version": version, "sections": parts, "general": general, "comment_total": len(comments),
        "can_comment": services.can_comment(case, user, version),
        "decision": decision, "mode": mode if decision and mode in ("sign", "revise") else "",
        "is_pdf": is_pdf, "show_file": is_pdf and (request.GET.get("view") == "file" or parts is None),
        "signatures": list(version.signatures.select_related("signer")),
        "url": _panel_url(case, version),
        "close_url": reverse("case_detail", args=[case.pk]),
        "max_signature_mb": services.MAX_SIGNATURE_MB,
    }


@login_required
def case_detail(request, pk):
    case = _case_or_404(request.user, pk)
    user = request.user
    offers = services.available_actions(case, user)
    versions = list(services.visible_versions(case, user))
    stage = STAGE_OF.get(case.state)
    return render(request, "workflow/case_detail.html", {
        "case": case,
        "roles": [r.label for r in sorted(case.roles_of(user))],
        "offers": _action_buttons(case, offers),
        "cancel_offer": next((o for o in offers if o.transition.action == "cancel"), None),
        "can_edit": services.can_edit(case, user),
        "boxes": _doc_boxes(case, user, offers, versions),
        "panel": _panel(request, case, versions),
        "feedback": services.pending_feedback(case),
        "is_edu": services.sees_all_projects(user),
        "is_author": user.id == case.author_id,
        "assignments": case.referee_assignments.select_related("referee"),
        "deadlines": case.deadlines.all(),
        "events": services.visible_events(case, user),
        "phases": phases(case),
        "stage": stage, "stage_count": len(STAGES),
        "progress": round(100 * stage / len(STAGES)) if stage else None,
    })


@login_required
def case_edit(request, pk):
    case = _case_or_404(request.user, pk)
    if not services.can_edit(case, request.user):
        raise PermissionDenied
    form = forms.CaseEditForm(_posted(request), instance=case)
    if request.method == "POST" and form.is_valid():
        try:
            services.edit_case(case, request.user, **form.cleaned_data)
        except services.WorkflowError as e:
            form.add_error(None, str(e))
        else:
            messages.success(request, "عنوان پرونده به‌روز شد.")
            return redirect("case_detail", pk=case.pk)
    return render(request, "workflow/form.html", {
        "form": form, "case": case, "title": "ویرایش عنوان پرونده", "submit_label": "ذخیره"})


@login_required
def document_upload(request, pk):
    case = _case_or_404(request.user, pk)
    kind = services.uploadable_kind(case, request.user)
    if kind is None:
        raise PermissionDenied
    form = forms.UploadForm(_posted(request), request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            version = services.upload_document(case, request.user, kind, **form.cleaned_data)
        except services.WorkflowError as e:
            form.add_error(None, str(e))
        else:
            messages.success(request, f"{version.get_kind_display()} بارگذاری شد.")
            return redirect("case_detail", pk=case.pk)
    return render(request, "workflow/form.html", {
        "form": form, "case": case, "multipart": True, "submit_label": "بارگذاری",
        "title": f"بارگذاری {DocKind(kind).label}", "template_file": TEMPLATES[kind],
        "intro": "فایل را مطابق قالب نمونه آماده کنید. پس از بارگذاری، سامانه قالب آن را بررسی می‌کند و "
                 "اگر بخشی جا مانده باشد همین‌جا می‌گوید؛ بعد می‌توانید دوباره بارگذاری کنید یا همین نسخه را بفرستید."})


@login_required
@require_GET
def document_download(request, pk):
    """فایل‌ها از MEDIA_URL عمومی سرو نمی‌شوند؛ فقط کسی که پرونده را می‌بیند می‌تواند دانلود کند."""
    version = get_object_or_404(DocumentVersion, pk=pk)
    case = services.cases_for(request.user).filter(pk=version.case_id).first()
    if case is None or not services.can_download(case, request.user, version):
        raise Http404
    extension = version.file.name.rsplit(".", 1)[-1]
    return FileResponse(version.file.open("rb"), as_attachment=True,
                        filename=f"case{version.case_id}-{version.kind}-v{version.number}.{extension}")


@login_required
def case_action(request, pk, action):
    """یک ویو برای همه‌ی ۱۷ گذار ماشین‌حالت: فرم مناسب را نشان می‌دهد و perform() را صدا می‌زند."""
    case = _case_or_404(request.user, pk)
    offer = next((o for o in services.available_actions(case, request.user)
                  if o.transition.action == action), None)
    if offer is None:
        raise PermissionDenied
    if offer.blocked:
        messages.error(request, offer.blocked)
        return redirect("case_detail", pk=case.pk)

    if action in SIGN_ACTIONS:
        # تأیید سند فقط با امضای الکترونیک و از نمایشگر سند انجام می‌شود
        version = services.latest_version(case, DOC_ACTIONS[action])
        if version:
            return redirect(_panel_url(case, version, "sign"))

    t = offer.transition
    form = forms.action_form_class(action)(_posted(request), case=case, transition=t, user=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            services.perform(case, action, request.user, note=form.cleaned_data.get("note", ""),
                             **form.payload())
        except services.WorkflowError as e:
            form.add_error(None, str(e))
        else:
            messages.success(request, f"«{t.label}» انجام شد.")
            return redirect("case_detail", pk=case.pk)
    return render(request, "workflow/form.html", {
        "form": form, "case": case, "title": t.label,
        # تأیید، بازگرداندن و رد با نام خود اقدام تأیید می‌شوند تا کاربر بداند دقیقاً چه چیزی ثبت می‌شود
        "submit_label": t.label if t.tone != "primary" else "ثبت",
        "tone": t.tone,
        # فاز ۳: کادر «پیشنهاد سامانه» بالای فرم انتخاب استاد راهنما / تخصیص داور (فقط نمایش؛ انتخاب با انسان است)
        "suggestions": agents.suggestion_panel(case, SUGGESTION_PURPOSE[action], request.user)
                       if action in SUGGESTION_PURPOSE else None})


@login_required
def project_list(request):
    """فهرست همه‌ی پروژه‌ها با وضعیتشان، برای آموزش دانشکده؛ هر ردیف به صفحه‌ی جزئیات پرونده می‌رود."""
    if not services.sees_all_projects(request.user):
        raise PermissionDenied
    q = request.GET.get("q", "").strip()
    state = request.GET.get("state", "")
    cases = ProjectCase.objects.select_related("author", "advisor").order_by("-registration_date", "-pk")
    counts = {s: 0 for s in State}
    for s in cases.values_list("state", flat=True):
        counts[s] += 1
    if state in State.values:
        cases = cases.filter(state=state)
    if q:
        cases = cases.filter(Q(title_fa__icontains=q) | Q(title_en__icontains=q)
                             | Q(author__first_name__icontains=q) | Q(author__last_name__icontains=q)
                             | Q(author__username__icontains=q) | Q(author__student_number__icontains=q)
                             | Q(advisor__first_name__icontains=q) | Q(advisor__last_name__icontains=q))
    return render(request, "workflow/project_list.html", {
        "cases": cases, "q": q, "state": state,
        "state_counts": [(s, counts[s]) for s in State if counts[s]],
        "total": sum(counts.values()),
    })


# ───────────────────────── نمایشگر سند: نظر، تأیید با امضا، بازگرداندن، فایل‌ها ─────────────────────────

def _visible_version(user, pk):
    """نسخه‌ای که کاربر حق دیدنش را دارد، همراه پرونده‌اش؛ وگرنه ۴۰۴."""
    version = get_object_or_404(DocumentVersion.objects.select_related("case"), pk=pk)
    case = services.cases_for(user).filter(pk=version.case_id).first()
    if case is None or not services.can_download(case, user, version):
        raise Http404
    return case, version


@login_required
@require_POST
def document_comment(request, pk):
    case, version = _visible_version(request.user, pk)
    try:
        section = max(int(request.POST.get("section", 0)), 0)
    except ValueError:
        section = 0
    try:
        services.add_comment(case, request.user, version, request.POST.get("text", ""), section,
                             request.POST.get("section_title", ""))
    except services.NotAllowed:
        raise PermissionDenied from None
    except services.WorkflowError as e:
        messages.error(request, str(e))
    return redirect(f"{_panel_url(case, version)}#s-{section}")


@login_required
@require_POST
def document_decide(request, pk):
    case, version = _visible_version(request.user, pk)
    approve = request.POST.get("decision") == "approve"
    try:
        services.decide_document(case, request.user, version, approve, request.POST.get("note", ""),
                                 request.FILES.get("signature"))
    except services.NotAllowed:
        raise PermissionDenied from None
    except services.WorkflowError as e:
        messages.error(request, str(e))
        return redirect(_panel_url(case, version, "sign" if approve else "revise"))
    messages.success(request, f"{version.get_kind_display()} تأیید و امضای شما روی سند ثبت شد." if approve
                     else f"{version.get_kind_display()} برای اصلاح به دانشجو برگشت.")
    return redirect("case_detail", pk=case.pk)


CONTENT_TYPES = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                 "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


def _inline(field, filename=None):
    extension = field.name.rsplit(".", 1)[-1].lower()
    response = FileResponse(field.open("rb"), filename=filename, as_attachment=filename is not None,
                            content_type=CONTENT_TYPES.get(extension, "application/octet-stream"))
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
@require_GET
@xframe_options_sameorigin
def document_inline(request, pk):
    """فایل PDF برای نمایش درون نمایشگر (iframe)، با همان کنترل دسترسی دانلود."""
    _, version = _visible_version(request.user, pk)
    if not version.file.name.lower().endswith(".pdf"):
        raise Http404
    return _inline(version.file)


def _visible_signature(user, pk):
    signature = get_object_or_404(Signature.objects.select_related("version"), pk=pk)
    _visible_version(user, signature.version_id)
    return signature


@login_required
@require_GET
def signature_image(request, pk):
    return _inline(_visible_signature(request.user, pk).image)


@login_required
@require_GET
def signed_download(request, pk):
    signature = _visible_signature(request.user, pk)
    if not signature.signed_file:
        raise Http404
    return _inline(signature.signed_file, filename=signature.signed_file.name.rsplit("/", 1)[-1])
