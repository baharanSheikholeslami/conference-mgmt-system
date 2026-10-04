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
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.decorators.http import require_GET

from . import forms, rules, services
from .enums import DocKind, State
from .models import DocumentVersion

# ترتیب مسیر اصلی، برای نوار پیشرفت صفحه‌ی پرونده («اصلاح» هم‌ردیف «داوری» است)
STAGES = [State.ADVISOR_SELECTION, State.DRAFTING, State.ADVISOR_REVIEW, State.EDU_REGISTRATION,
          State.REFEREE_ASSIGNMENT, State.REFEREE_REVIEW, State.GROUP_APPROVAL,
          State.COUNCIL_APPROVAL, State.IN_PROGRESS, State.THESIS_ADVISOR_REVIEW,
          State.THESIS_SUBMITTED, State.DEFENSE_SCHEDULED, State.COMPLETED]
STAGE_OF = {state: i for i, state in enumerate(STAGES, 1)} | {
    State.REVISION: STAGES.index(State.REFEREE_REVIEW) + 1}


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

    @property
    def todo(self):
        return [o for o in self.offers if o.pending]


class PasswordChange(SuccessMessageMixin, auth_views.PasswordChangeView):
    """کاربر رمز اولیه‌ای را که ادمین داده عوض می‌کند (ویوی آماده‌ی جنگو + قالب فرم خودمان)."""
    template_name = "workflow/form.html"
    success_url = reverse_lazy("dashboard")
    success_message = "رمز عبور شما تغییر کرد."
    extra_context = {"title": "تغییر رمز عبور", "submit_label": "ذخیره‌ی رمز جدید"}


@login_required
def dashboard(request):
    user = request.user
    rows = [Row(case=c, roles=[r.label for r in sorted(c.roles_of(user))],
                offers=services.available_actions(c, user), deadline=services.next_deadline(c))
            for c in services.cases_for(user)]
    return render(request, "workflow/dashboard.html", {
        "pending": [r for r in rows if r.todo],
        "others": [r for r in rows if not r.todo],
        "can_create": services.can_create_case(user),
    })


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


@login_required
def case_detail(request, pk):
    case = _case_or_404(request.user, pk)
    user = request.user
    offers = services.available_actions(case, user)
    stage = STAGE_OF.get(case.state)
    return render(request, "workflow/case_detail.html", {
        "case": case,
        "roles": [r.label for r in sorted(case.roles_of(user))],
        "offers": [o for o in offers if o.transition.action != "cancel"],
        "cancel_offer": next((o for o in offers if o.transition.action == "cancel"), None),
        "upload_kind": services.uploadable_kind(case, user),
        "can_edit": services.can_edit(case, user),
        "versions": case.versions.select_related("uploaded_by")
                        .prefetch_related("reviews__assignment__referee").order_by("kind", "-number"),
        "assignments": case.referee_assignments.select_related("referee"),
        "deadlines": case.deadlines.all(),
        "events": case.events.select_related("actor"),
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
        "title": f"بارگذاری {DocKind(kind).label}"})


@login_required
@require_GET
def document_download(request, pk):
    """فایل‌ها از MEDIA_URL عمومی سرو نمی‌شوند؛ فقط کسی که پرونده را می‌بیند می‌تواند دانلود کند."""
    version = get_object_or_404(DocumentVersion, pk=pk)
    if not services.cases_for(request.user).filter(pk=version.case_id).exists():
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

    t = offer.transition
    form = forms.action_form_class(action)(_posted(request), case=case, transition=t)
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
        "form": form, "case": case, "title": t.label, "submit_label": "ثبت",
        "danger": action in ("cancel", "group_reject", "council_reject")})
