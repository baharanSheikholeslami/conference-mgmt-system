"""
نمایشگر سند درون صفحه‌ی پرونده: متن سند به «بخش‌ها» شکسته می‌شود تا استاد روی هر بخش نظر بگذارد.

متن با همان استخراج‌کننده‌ی عامل‌ها خوانده می‌شود (agents/extract.py) و مرز بخش‌ها با همان تشخیص
عنوان‌های قطعه‌بندی RAG («۱. مقدمه»، «فصل ۲: روش»، …). فایل اصلی دست نمی‌خورد؛ این فقط نمایش است.
"""
from dataclasses import dataclass, field

from agents import chunking, extract

INTRO_TITLE = "ابتدای سند"


@dataclass
class Section:
    index: int
    title: str
    lines: list = field(default_factory=list)
    comments: list = field(default_factory=list)


def sections(version):
    """فهرست بخش‌های سند، یا None اگر متن فایل قابل‌خواندن نباشد (مثلاً PDF اسکن‌شده)."""
    try:
        text = extract.extract_text(version.file)
    except extract.ExtractionError:
        return None
    if not text.strip():
        return None
    result = [Section(1, INTRO_TITLE)]          # شماره‌ی ۰ برای نظر درباره‌ی «کل سند» است
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        title = chunking.heading_ref(line)
        if title:
            result.append(Section(len(result) + 1, title))
        else:
            result[-1].lines.append(line)
    return result                               # ابتدای سندِ خالی در قالب نمایش داده نمی‌شود


def with_comments(parts, comments):
    """نظرها را زیر بخش خودشان می‌گذارد؛ نظر «کل سند» (بخش ۰) یا بخشی که پیدا نشد جدا برمی‌گردد."""
    by_index = {s.index: s for s in parts or []}
    general = []
    for c in comments:
        (by_index[c.section].comments if c.section in by_index else general).append(c)
    return general
