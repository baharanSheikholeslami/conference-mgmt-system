"""ویوهای کاربری عامل‌ها: جعبه‌ی گفتگوی دستیار و دکمه‌های «تلاش دوباره»."""
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from workflow import services as wf
from workflow.models import DocumentVersion

from . import defense, services
from .models import DefensePrep, MockAnswer


def _require_agents(user):
    """عامل‌ها خاموش‌اند، یا در حالت سایه این کاربر خروجی آن‌ها را نمی‌بیند: این مسیرها برایش وجود ندارند."""
    if not services.visible_to(user):
        raise Http404


@login_required
@require_POST
def assistant_ask(request):
    """جعبه‌ی گفتگو با fetch این‌جا را صدا می‌زند؛ پاسخ همیشه JSON است."""
    _require_agents(request.user)
    try:
        question = json.loads(request.body or b"{}").get("question", "")
    except (ValueError, AttributeError):
        question = ""
    try:
        return JsonResponse({"ok": True, **services.ask_assistant(request.user, question)})
    except services.AgentError as e:
        return JsonResponse({"ok": False, "error": str(e)})


@login_required
@require_POST
def validation_retry(request, pk):
    _require_agents(request.user)
    version = get_object_or_404(DocumentVersion, pk=pk)
    if not wf.cases_for(request.user).filter(pk=version.case_id).exists():
        raise Http404
    if not services.can_retry_validation(version, request.user):
        raise PermissionDenied
    services.request_validation(version)
    messages.info(request, "بررسی خودکار قالب دوباره آغاز شد.")
    return redirect("case_detail", pk=version.case_id)


@login_required
@require_POST
def suggestions_refresh(request, pk):
    _require_agents(request.user)
    case = get_object_or_404(wf.cases_for(request.user), pk=pk)
    if not services.can_refresh_suggestions(case, request.user):
        raise PermissionDenied
    services.request_referee_suggestions(case)
    messages.info(request, "پیشنهاد داور دوباره درخواست شد؛ چند لحظه بعد صفحه را تازه کنید.")
    return redirect("case_action", pk=case.pk, action="assign_referees")


@login_required
@require_POST
def advisor_suggestions_request(request, pk):
    """دانشجو علایقش را می‌نویسد و از سامانه پیشنهاد استاد راهنما می‌خواهد (فرم بالای «انتخاب استاد راهنما»)."""
    _require_agents(request.user)
    case = get_object_or_404(wf.cases_for(request.user), pk=pk)
    if not services.can_request_advisor_suggestions(case, request.user):
        raise PermissionDenied
    try:
        services.request_advisor_suggestions(case, request.POST.get("interests", ""))
    except services.AgentError as e:
        messages.error(request, str(e))
    else:
        messages.info(request, "درخواست پیشنهاد استاد راهنما ثبت شد؛ نتیجه چند لحظه بعد همین‌جا نمایش داده می‌شود.")
    return redirect("case_action", pk=case.pk, action="select_advisor")


# ───────────────────────── آماده‌سازی دفاع ─────────────────────────

def _defense_case(request, pk):
    _require_agents(request.user)
    case = get_object_or_404(wf.cases_for(request.user), pk=pk)
    if not services.defense_prep_visible_to(request.user, case):
        raise Http404
    return case


@login_required
def defense_prep_page(request, pk):
    """صفحه‌ی «آماده‌سازی دفاع»: پرسش‌های محتمل، نقاط ضعف و جلسه‌ی دفاع آزمایشی."""
    case = _defense_case(request, pk)
    card = services.defense_card(case, request.user)
    prep = card["prep"] if card else None
    groups, answers = [], {}
    if prep:
        for a in prep.answers.all():
            answers.setdefault(a.question_id, []).append(a)
        by_category = {}
        for q in prep.questions:
            by_category.setdefault(q["category"], []).append({**q, "answers": answers.get(q["id"], [])})
        groups = [{"key": key, "label": defense.CATEGORY_LABELS[key], "questions": by_category[key]}
                  for key in defense.CATEGORY_KEYS + [defense.GENERAL] if key in by_category]
    waiting = bool(prep) and (prep.is_waiting or any(a.is_waiting for qs in answers.values() for a in qs))
    return render(request, "agents/defense_prep.html", {
        "case": case, "card": card, "prep": prep, "groups": groups, "waiting": waiting,
        "can_start": bool(card) and not (prep and prep.is_waiting),
        "can_practice": bool(prep) and services.can_practice(prep, request.user) and not waiting,
        "severities": defense.SEVERITIES, "min_answer": defense.MIN_ANSWER_CHARS,
        "max_answer": defense.MAX_ANSWER_CHARS,
    })


@login_required
@require_POST
def defense_prep_start(request, pk):
    case = _defense_case(request, pk)
    if not services.can_request_defense_prep(case, request.user):
        raise PermissionDenied
    current = DefensePrep.objects.filter(version=services.defense_thesis(case)).first()
    if current and current.is_waiting:
        messages.info(request, "آماده‌سازی دفاع در جریان است؛ چند لحظه صبر کنید.")
    else:
        services.request_defense_prep(case)
        messages.info(request, "آماده‌سازی دفاع آغاز شد؛ خواندن کل پایان‌نامه ممکن است چند دقیقه طول بکشد.")
    return redirect("defense_prep", pk=case.pk)


@login_required
@require_POST
def defense_mock_answer(request, pk):
    """پاسخ تمرینی دانشجو به یک پرسش (یا به پرسش تکمیلیِ یک پاسخ قبلی)."""
    _require_agents(request.user)
    prep = get_object_or_404(DefensePrep.objects.select_related("version__case"), pk=pk)
    case = get_object_or_404(wf.cases_for(request.user), pk=prep.version.case_id)
    if not services.can_practice(prep, request.user):
        raise PermissionDenied
    follow_up_of = None
    if request.POST.get("follow_up_of"):
        follow_up_of = MockAnswer.objects.filter(pk=request.POST["follow_up_of"], prep=prep).first() \
            if request.POST["follow_up_of"].isdigit() else None
        if follow_up_of is None:
            raise Http404
    question_id = request.POST.get("question_id", "")
    try:
        services.request_mock_feedback(prep, question_id, request.POST.get("answer", ""), follow_up_of)
    except services.AgentError as e:
        messages.error(request, str(e))
    else:
        messages.info(request, "پاسخ شما ثبت شد؛ بازخورد چند لحظه بعد همین‌جا نمایش داده می‌شود.")
    anchor = f"#{question_id}" if question_id[1:].isdigit() and question_id.startswith("q") else ""
    return redirect(reverse("defense_prep", args=[case.pk]) + anchor)
