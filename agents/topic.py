"""
استخراج «عنوان / موضوع» پروژه از متن پیشنهاد — بخش قطعی و بدون هوش مصنوعی.

در جریان جدید، دانشجو هنگام ثبت پرونده موضوعی انتخاب نمی‌کند؛ موضوع پس از بارگذاری پیشنهاد
از متن آن استخراج و در پرونده نوشته می‌شود. دو لایه دارد:
  ۱. همین فایل (هم‌زمان با بارگذاری): فیلد «عنوان فارسی / عنوان انگلیسی» فرم پیشنهاد، و اگر
     نبود، نخستین سطر عنوان‌مانند سند (حدسی). این لایه بدون n8n هم کار می‌کند.
  ۲. عامل استخراج موضوع (n8n + مدل زبانی، services.request_topic_extraction): عنوان را از
     متن می‌خواند؛ پورتال بررسی می‌کند که عنوان پیشنهادی واقعاً در متن آمده باشد (verified).
دانشجو و استاد راهنما همیشه می‌توانند عنوان را اصلاح کنند و عنوان دستی دیگر بازنویسی نمی‌شود.
"""
import re
from dataclasses import dataclass

from workflow.enums import TitleSource

from .extract import normalize

MIN_TITLE_CHARS = 8
MAX_TITLE_CHARS = 250
_PLACEHOLDER = re.compile(r"^[\s.…_\-–—:]*$")
_LABEL_FA = re.compile(r"^\s*عنوان\s*(?:فارسی|پروژه(?:\s*به\s*فارسی)?)?\s*[:：]\s*(.*)$", re.M)
_LABEL_EN = re.compile(r"^\s*(?:عنوان\s*(?:انگلیسی|لاتین)|English\s*title|Title)\s*[:：]\s*(.*)$",
                       re.M | re.I)
# سطرهایی که هرگز عنوان پروژه نیستند (سرصفحه‌ی فرم، برچسب‌ها، عنوان بخش‌ها)
_NOT_A_TITLE = re.compile(
    r"(فرم|دانشکده|دانشگاه|مشخصات|استاد|نام و نام خانوادگی|شماره دانشجویی|رایانامه|نیم‌سال|"
    r"تعریف پروژه|مقدمه|بسمه|^\d|^[۰-۹]|امضا|\|)")


@dataclass(frozen=True)
class FoundTitle:
    title_fa: str
    title_en: str
    source: str


def _value(match):
    if not match:
        return ""
    value = " ".join(match.group(1).split()).strip(" .:؛،")
    return "" if _PLACEHOLDER.match(value) or len(value) < MIN_TITLE_CHARS else value[:MAX_TITLE_CHARS]


def title_from_text(text):
    """عنوان را از متن پیشنهاد پیدا می‌کند؛ None اگر هیچ عنوانی پیدا نشد."""
    text = normalize(text or "")
    if not text:
        return None
    title_fa = _value(_LABEL_FA.search(text))
    title_en = _value(_LABEL_EN.search(text))
    if title_fa:
        return FoundTitle(title_fa, title_en, TitleSource.HEADING)
    # نخستین سطر عنوان‌مانند در ابتدای سند (حدسی؛ عامل هوش مصنوعی یا انسان اصلاحش می‌کند)
    for line in text.splitlines()[:40]:
        line = line.strip()
        if MIN_TITLE_CHARS <= len(line) <= 160 and not _NOT_A_TITLE.search(line) \
                and not line.endswith((".", "؟", "!")):
            return FoundTitle(line[:MAX_TITLE_CHARS], title_en, TitleSource.FIRST_LINE)
    return None


def _squash(text):
    return re.sub(r"[^\w]+", " ", normalize(text).lower()).strip()


def appears_in(title, text):
    """آیا عنوان (پس از یکدست‌سازی) عیناً در متن سند آمده است؟ — راستی‌آزمایی خروجی مدل زبانی."""
    title = _squash(title)
    return len(title) >= MIN_TITLE_CHARS and title in _squash(text)
