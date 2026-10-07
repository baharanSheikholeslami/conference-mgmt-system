"""
کلاینت کوچک n8n: تنها راه صحبت پورتال با لایه‌ی عامل‌ها.

پورتال هیچ‌وقت مستقیم با مدل زبانی حرف نمی‌زند؛ فقط یک Webhook در n8n را صدا می‌زند و
n8n است که مدل زبانی، مدل embedding و ایمیل را هماهنگ می‌کند.
عمداً با کتابخانه‌ی استاندارد پایتون نوشته شده تا وابستگی تازه‌ای اضافه نشود.
"""
import json
import urllib.error
import urllib.request

from django.conf import settings

TOKEN_HEADER = "X-Agent-Token"


class Unavailable(Exception):
    """n8n در دسترس نیست، خطا داده یا پاسخ نامعتبر برگردانده است."""


def post(path, payload, timeout=10):
    """POST {N8N_WEBHOOK_URL}/{path} با بدنه‌ی JSON. خروجی: پاسخ JSON (dict)."""
    url = f"{settings.N8N_WEBHOOK_URL}/{path}"
    request = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json; charset=utf-8",
                 TOKEN_HEADER: settings.AGENT_TOKEN})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        hint = " (گردش‌کار در n8n فعال است؟)" if e.code == 404 else ""
        raise Unavailable(f"n8n به {path} پاسخ HTTP {e.code} داد{hint}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise Unavailable(f"n8n در {url} در دسترس نیست: {getattr(e, 'reason', e)}") from e
    try:
        data = json.loads(body) if body.strip() else {}
    except ValueError as e:
        raise Unavailable(f"پاسخ n8n به {path} JSON معتبر نبود") from e
    return data if isinstance(data, dict) else {"data": data}


def healthy(timeout=3):
    """آیا خود n8n بالا است؟ (نقطه‌ی سلامت /healthz در ریشه‌ی n8n)"""
    root = settings.N8N_WEBHOOK_URL.rsplit("/", 1)[0]
    try:
        with urllib.request.urlopen(f"{root}/healthz", timeout=timeout) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False
