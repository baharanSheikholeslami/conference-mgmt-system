"""ویوهای کاربری عامل‌ها: جعبه‌ی گفتگوی دستیار و دکمه‌های «تلاش دوباره»."""
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from workflow import services as wf
from workflow.enums import DocKind
from workflow.models import DocumentVersion

from . import services


def _require_agents():
    if not services.enabled():
        raise Http404


@login_required
@require_POST
def assistant_ask(request):
    """جعبه‌ی گفتگو با fetch این‌جا را صدا می‌زند؛ پاسخ همیشه JSON است."""
    _require_agents()
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
    _require_agents()
    version = get_object_or_404(DocumentVersion, pk=pk, kind=DocKind.PROPOSAL)
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
    _require_agents()
    case = get_object_or_404(wf.cases_for(request.user), pk=pk)
    if not services.can_refresh_suggestions(case, request.user):
        raise PermissionDenied
    services.request_referee_suggestions(case)
    messages.info(request, "پیشنهاد داور دوباره درخواست شد؛ چند لحظه بعد صفحه را تازه کنید.")
    return redirect("case_action", pk=case.pk, action="assign_referees")
