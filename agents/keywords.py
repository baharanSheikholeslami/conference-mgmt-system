"""
رتبه‌بندی پشتیبانِ کلیدواژه‌ای برای پیشنهاد استاد (بدون هوش مصنوعی).

مسیر اصلی پیشنهاد استاد راهنما و داور، تطبیق معنایی با embedding در n8n است (گردش‌کار
suggest-referees). اگر لایه‌ی عامل‌ها خاموش باشد، n8n در دسترس نباشد یا مدل embedding پاسخ
ندهد، پورتال همین رتبه‌بندی ساده را خودش انجام می‌دهد تا دانشجو هیچ‌وقت بدون پیشنهاد نماند:
واژه‌های مشترک متن دانشجو و «زمینه‌های تخصصی» هر استاد شمرده می‌شود.
"""
import math
import re

from .extract import normalize

MODEL_NAME = "keyword-fallback"

# واژه‌های پرتکرار و بی‌معنا برای تطبیق (فارسی و انگلیسی)
STOPWORDS = set("""
و در به از با که را این آن برای تا یا هم اما اگر بر می‌خواهم میخواهم می‌خواهم خواهم کنم کار روی
مبتنی پایه بر اساس یک های ها ای است هست بود شود می‌شود کردن کرده دارم دارد علاقه علاقه‌مند
علاقمند مند زمینه حوزه موضوع پروژه سامانه سیستم طراحی پیاده‌سازی پیاده سازی استفاده مورد نظر
خیلی بیشتر خود من ما هر چند دیگر
the a an and or of in on for to with by based using about my i want work
""".split())
_TOKEN = re.compile(r"[\w‌]+")


def tokens(text):
    return [stem for stem, _ in _pairs(text)]


def _pairs(text):
    """[(ریشه, واژه‌ی اصلی)] — ریشه برای تطبیق، واژه‌ی اصلی برای نمایش به کاربر."""
    words = []
    for raw in _TOKEN.findall(normalize(text or "").lower()):
        word = raw.replace("‌", "")
        for suffix in ("های", "ها", "ی"):          # ریشه‌یابی بسیار سبک فارسی
            if word.endswith(suffix) and len(word) - len(suffix) >= 3:
                word = word[: -len(suffix)]
                break
        if len(word) >= 2 and word not in STOPWORDS and raw not in STOPWORDS and not word.isdigit():
            words.append((word, raw))
    return words


def _match(a, b):
    """دو واژه یکی‌اند اگر برابر باشند یا یکی پیشوند دیگری با دست‌کم چهار حرف باشد."""
    if a == b:
        return True
    short, long_ = sorted((a, b), key=len)
    return len(short) >= 4 and long_.startswith(short)


def rank(query, candidates):
    """
    candidates: [{"id", "expertise"}]. خروجی هم‌شکل پاسخ n8n:
    [{"referee_id", "score", "reason"}] (score=None یعنی تخصص ثبت نشده).
    """
    q = list(dict.fromkeys(tokens(query)))
    rows = []
    for c in candidates:
        expertise = (c.get("expertise") or "").strip()
        if not expertise:
            rows.append({"referee_id": c["id"], "score": None,
                         "reason": "زمینه‌ی تخصصی این استاد در سامانه ثبت نشده است."})
            continue
        pairs = list(dict(_pairs(expertise)).items())       # یک بار برای هر ریشه
        e = [stem for stem, _ in pairs]
        shared = [raw for stem, raw in pairs if any(_match(stem, x) for x in q)]
        score = len(shared) / math.sqrt(len(q) * len(e)) if q and e else 0.0
        reason = ("واژه‌های مشترک با متن شما: " + "، ".join(shared[:6])) if shared \
            else "واژه‌ی مشترکی میان متن شما و زمینه‌های تخصصی این استاد پیدا نشد."
        rows.append({"referee_id": c["id"], "score": round(min(1.0, score), 4), "reason": reason})
    return rows
