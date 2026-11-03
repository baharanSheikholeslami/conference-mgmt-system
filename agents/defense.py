"""
عامل ۵ — آماده‌سازی دفاع: داده‌ها و توابع خالص (بدون پایگاه‌داده و شبکه).

این فایل مثل checklist.py «تنها منبع حقیقت» عامل است: دسته‌بندی پرسش‌ها، سقف‌ها و قاعده‌ی
راستی‌آزمایی نقل‌قول‌ها همین‌جاست. n8n دسته‌ها را از payload می‌گیرد و به هیچ دسته‌ی خاصی وابسته نیست.

راستی‌آزمایی (find_quote): مدل زبانی برای هر پرسش و هر نقطه‌ضعف باید جمله‌ای «عیناً» از متن
پایان‌نامه (یا پیشنهاد) نقل کند. پورتال خودش آن نقل‌قول را در متن واقعی جست‌وجو می‌کند؛ اگر پیدا نشد،
مورد با برچسب «شاهد در متن پیدا نشد» نمایش داده می‌شود تا دانشجو به ادعای ساختگی مدل اعتماد نکند.
"""
import re
import unicodedata
from collections import Counter

# key: شناسه‌ی پایدار | label: نام نمایشی | hint: راهنمای مدل زبانی برای این دسته
CATEGORIES = [
    ("method", "روش و طراحی",
     "چرا این روش/معماری انتخاب شد، فرض‌ها، جایگزین‌ها و جزئیاتی که در متن مبهم مانده است."),
    ("evaluation", "ارزیابی و نتایج",
     "اعتبار آزمایش‌ها، معیارها، داده‌ی آزمون، مقایسه با مبنا، و این‌که نتیجه‌ها ادعا را ثابت می‌کنند یا نه."),
    ("novelty", "نوآوری و کارهای مرتبط",
     "تفاوت با کارهای مشابه، سهم واقعی دانشجو، و مرجع‌هایی که مقایسه با آن‌ها ناقص است."),
    ("proposal_gap", "فاصله‌ی پیشنهاد و پایان‌نامه",
     "هدف یا روش ارزیابی‌ای که در پیشنهاد وعده داده شده اما در پایان‌نامه دیده نمی‌شود یا تغییر کرده است."),
    ("limitations", "محدودیت‌ها و کارهای آینده",
     "نقاط ضعف، تعمیم‌پذیری، مقیاس‌پذیری، تهدیدهای اعتبار و این‌که با زمان بیشتر چه می‌کردید."),
]
CATEGORY_KEYS = [k for k, _, _ in CATEGORIES]
CATEGORY_LABELS = dict((k, label) for k, label, _ in CATEGORIES)
GENERAL = "general"
CATEGORY_LABELS[GENERAL] = "پرسش کلی"

SEVERITIES = {"high": "جدی", "medium": "متوسط", "low": "جزئی"}

MAX_QUESTIONS = 12
MAX_WEAK_POINTS = 6
MAX_QUESTION_CHARS = 400
MAX_TEXT_CHARS = 700
MAX_QUOTE_CHARS = 300
MIN_ANSWER_CHARS = 20
MAX_ANSWER_CHARS = 3000
CONTEXT_CHARS = 1500          # متن پیرامون نقل‌قول که برای ارزیابی پاسخ تمرینی فرستاده می‌شود
FUZZY_MIN = 0.75              # سهم واژه‌های نقل‌قول که باید در یک پنجره از متن باشند

VERDICTS = {"strong": "پاسخ قوی", "partial": "پاسخ ناقص", "weak": "پاسخ ضعیف"}

_FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_ARABIC = str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک", "ة": "ه", "أ": "ا", "إ": "ا", "آ": "ا"})
_DROP = re.compile("[‌‍‎‏ـً-ٰٟ]")


def categories_payload():
    return [{"key": k, "label": label, "hint": hint} for k, label, hint in CATEGORIES]


def _norm_char(ch):
    """یک نویسه ← شکل یکدست آن (رشته‌ی خالی یعنی حذف، فاصله یعنی جداکننده)."""
    if _DROP.match(ch):
        return ""
    out = []
    for c in unicodedata.normalize("NFKC", ch).translate(_ARABIC).translate(_FA_DIGITS).lower():
        if _DROP.match(c):
            continue
        out.append(c if c.isalnum() else " ")
    return "".join(out)


def normalize_with_map(text):
    """
    متن یکدست‌شده (فقط حرف و رقم، با یک فاصله میان واژه‌ها) و نگاشت هر نویسه‌ی آن به جای
    نویسه‌ی متناظر در متن اصلی؛ تا پس از یافتن نقل‌قول بتوان متن پیرامون آن را از متن اصلی برداشت.
    """
    chars, positions = [], []
    for i, ch in enumerate(text or ""):
        for c in _norm_char(ch):
            if c == " " and (not chars or chars[-1] == " "):
                continue
            chars.append(c)
            positions.append(i)
    while chars and chars[-1] == " ":
        chars.pop()
        positions.pop()
    return "".join(chars), positions


def normalize(text):
    return normalize_with_map(text)[0]


def find_quote(text, quote, index=None):
    """
    جای نقل‌قول در متن (شماره‌ی نویسه در متن اصلی) یا None اگر پیدا نشد.
    ۱. تطبیق دقیق پس از یکدست‌سازی (نیم‌فاصله، ی/ک عربی، ارقام فارسی، نشانه‌گذاری)
    ۲. تطبیق تقریبی: پنجره‌ای از متن با همان تعداد واژه که دست‌کم FUZZY_MIN واژه‌های نقل‌قول را دارد
       (مدل‌های کوچک گاهی یک واژه را جا می‌اندازند یا عوض می‌کنند)
    index خروجی normalize_with_map(text) است تا برای چند نقل‌قول یک‌بار ساخته شود.
    """
    normalized, positions = index or normalize_with_map(text)
    q = normalize(quote)
    if len(q) < 8 or not normalized:
        return None
    at = normalized.find(q)
    if at >= 0:
        return positions[at]
    # نیم‌فاصله گاهی فاصله، گاهی هیچ نوشته می‌شود («نتیجه‌ی» / «نتیجه ی» / «نتیجهی»): بدون فاصله‌ها هم بسنج
    compact = [(c, p) for c, p in zip(normalized, positions) if c != " "]
    at = "".join(c for c, _ in compact).find(q.replace(" ", ""))
    if at >= 0:
        return compact[at][1]
    words = [w for w in q.split(" ") if w]
    if len(words) < 3:
        return None
    # واژه‌های متن همراه با جای شروع هرکدام
    spans = [(m.group(), m.start()) for m in re.finditer(r"\S+", normalized)]
    size = len(words)
    if len(spans) < size:
        return None
    need, needed = Counter(words), len(words)
    window = Counter(w for w, _ in spans[:size])
    best, best_at = 0, 0
    for start in range(len(spans) - size + 1):
        if start:
            window[spans[start - 1][0]] -= 1
            window[spans[start + size - 1][0]] += 1
        hit = sum(min(n, window[w]) for w, n in need.items())
        if hit > best:
            best, best_at = hit, start
            if hit == needed:
                break
    if best / needed >= FUZZY_MIN:
        return positions[spans[best_at][1]]
    return None


def context_around(text, position, size=CONTEXT_CHARS):
    """تکه‌ای از متن به طول تقریبی size که position در یک‌سوم ابتدایی آن است."""
    if not text:
        return ""
    if position is None:
        return text[:size]
    start = max(0, position - size // 3)
    return text[start:start + size]


def clean_text(value, limit):
    return " ".join(str(value or "").split())[:limit]
