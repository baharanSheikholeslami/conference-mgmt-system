"""
قطعه‌بندی سند رویه برای نمایه‌سازی RAG.

هدف این است که هر قطعه «یک بند قابل‌استناد» باشد: متن از روی عنوان‌ها و شماره‌ی بندها شکسته
می‌شود و برچسب همان عنوان/بند (ref) کنار قطعه می‌ماند تا دستیار بتواند بگوید پاسخ را از کجا آورده است.
توابع این فایل خالص‌اند (بدون پایگاه‌داده و شبکه).
"""
import re
from dataclasses import dataclass

MAX_CHARS = 900        # حداکثر طول یک قطعه؛ بندهای بلندتر در مرز جمله شکسته می‌شوند
MIN_CHARS = 40         # قطعه‌های کوتاه‌تر از این به قطعه‌ی قبلی می‌چسبند
REF_CHARS = 70

_DIGIT = "0-9۰-۹"
_MARKDOWN = re.compile(r"^#{1,6}\s+(.*)$")
_KEYWORD = re.compile(rf"^(ماده|بند|تبصره|توضیح|فصل|بخش|پیوست)\s*[{_DIGIT}]+")
# شماره‌ی چندسطحی مثل ۵-۲ یا 4.1.3
_MULTI_LEVEL = re.compile(rf"^([{_DIGIT}]+(?:[\-–.٫/][{_DIGIT}]+)+)[\s.:\-–)]")
# شماره‌ی تک‌سطحی مثل «۳- تعاریف»؛ فقط وقتی سطر کوتاه است عنوان حساب می‌شود (وگرنه یک مورد از فهرست است)
_SINGLE_LEVEL = re.compile(rf"^[{_DIGIT}]+\s*[.\-–)]\s*\S")
_SENTENCE_END = re.compile(r"(?<=[.؟!؛:])\s+")


@dataclass(frozen=True)
class Chunk:
    ref: str
    text: str


def _short(line):
    return line if len(line) <= REF_CHARS else line[:REF_CHARS].rstrip() + "…"


def heading_ref(line):
    """اگر سطر شروع یک بند/عنوان است برچسب مرجع آن را برمی‌گرداند، وگرنه None."""
    line = line.strip()
    markdown = _MARKDOWN.match(line)
    if markdown:
        return _short(markdown.group(1).strip())
    if _KEYWORD.match(line):
        head = re.split(r"[:：]", line, maxsplit=1)[0]
        return _short(head if len(head) <= REF_CHARS else line)
    multi = _MULTI_LEVEL.match(line)
    if multi:
        return _short(line) if len(line) <= REF_CHARS else f"بند {multi.group(1)}"
    if _SINGLE_LEVEL.match(line) and len(line) <= REF_CHARS:
        return line
    return None


def _wrap(text):
    """متنِ بلندتر از MAX_CHARS را در مرز جمله‌ها می‌شکند."""
    if len(text) <= MAX_CHARS:
        return [text]
    pieces, current = [], ""
    for sentence in _SENTENCE_END.split(text):
        while len(sentence) > MAX_CHARS:                 # جمله‌ی خیلی بلند: برش سخت
            pieces.append(sentence[:MAX_CHARS])
            sentence = sentence[MAX_CHARS:]
        if current and len(current) + 1 + len(sentence) > MAX_CHARS:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return pieces


def split(text):
    """متن یکدست‌شده ← فهرست Chunk به ترتیب سند."""
    sections, ref, lines = [], "", []

    def flush():
        body = " ".join(lines).strip()
        if body:
            sections.append((ref, body))

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        found = heading_ref(line)
        if found:
            flush()
            ref, lines = found, []
            if not _MARKDOWN.match(line):               # سطر بند، خودش بخشی از متن بند است
                lines.append(line)
        else:
            lines.append(line)
    flush()

    chunks = []
    for ref, body in sections:
        for piece in _wrap(body):
            if chunks and len(piece) < MIN_CHARS and chunks[-1].ref == ref:
                chunks[-1] = Chunk(ref, f"{chunks[-1].text} {piece}")
            else:
                chunks.append(Chunk(ref, piece))
    return chunks
