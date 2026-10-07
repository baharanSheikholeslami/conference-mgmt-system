"""
API داخلی پورتال برای n8n (مسیرهای /api/agents/…).

این‌ها «ابزار»های عامل‌ها هستند: n8n از این راه نتیجه‌ی کارش را برمی‌گرداند یا داده می‌گیرد.
کاربر انسانی با این مسیرها کاری ندارد؛ به‌جای نشست و CSRF، با توکن مشترک (سرآیند
X-Agent-Token برابر AGENT_TOKEN در .env) محافظت می‌شوند. هیچ‌کدام وضعیت پرونده را تغییر نمی‌دهند.
"""
import json
from datetime import date
from functools import wraps

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.crypto import constant_time_compare
from django.views.decorators.csrf import csrf_exempt

from . import services
from .models import DocumentValidation, SuggestionRun
from .n8n import TOKEN_HEADER


def agent_api(method):
    """توکن و متد را بررسی می‌کند، بدنه‌ی JSON را می‌خواند و خروجی dict ویو را JSON می‌کند."""
    def decorator(view):
        @csrf_exempt
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            token = request.headers.get(TOKEN_HEADER, "")
            if not settings.AGENT_TOKEN or not constant_time_compare(token, settings.AGENT_TOKEN):
                return JsonResponse({"error": "توکن عامل نامعتبر است."}, status=403)
            if request.method != method:
                return JsonResponse({"error": f"فقط {method} مجاز است."}, status=405)
            data = {}
            if method == "POST":
                try:
                    data = json.loads(request.body or b"{}")
                except ValueError:
                    data = None
                if not isinstance(data, dict):
                    return JsonResponse({"error": "بدنه باید یک شیء JSON باشد."}, status=400)
            try:
                return JsonResponse(view(request, data, *args, **kwargs))
            except services.AgentError as e:
                return JsonResponse({"error": str(e)}, status=409)
        return wrapper
    return decorator


@agent_api("POST")
def validation_result(request, data, pk):
    validation = services.save_validation_result(get_object_or_404(DocumentValidation, pk=pk), data)
    return {"status": validation.status}


@agent_api("POST")
def referee_suggestions_result(request, data, pk):
    run = services.save_referee_suggestions(get_object_or_404(SuggestionRun, pk=pk), data)
    return {"status": run.status, "saved": run.suggestions.count()}


@agent_api("POST")
def rag_search(request, data):
    return {"chunks": services.search_policy(data.get("embedding"), str(data.get("model") or ""),
                                             data.get("k"))}


@agent_api("GET")
def reminders_due(request, data):
    """?today=YYYY-MM-DD تاریخ را شبیه‌سازی می‌کند (برای آزمون خط‌زمانی‌ها در فاز ۵)."""
    today = None
    if request.GET.get("today"):
        try:
            today = date.fromisoformat(request.GET["today"])
        except ValueError:
            raise services.AgentError("today باید به شکل YYYY-MM-DD باشد.") from None
    notifications = services.due_reminders(today)
    return {"count": len(notifications), "notifications": notifications}


@agent_api("POST")
def notification_record(request, data):
    notification = services.record_notification(data)
    return {"id": notification.pk, "status": notification.status}
