"""
استخراج متن از فایل‌های بارگذاری‌شده (PDF و DOCX) و یکدست‌سازی متن فارسی.

مدل زبانی فایل را نمی‌بیند؛ فقط متنی را می‌بیند که این‌جا استخراج می‌شود. پس هر چیزی که
این‌جا از دست برود (مثلاً متنِ داخل تصویر در PDF اسکن‌شده) برای عامل‌ها «وجود ندارد».
"""
import io
import re
import unicodedata
from pathlib import Path

_ARABIC_TO_PERSIAN = str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک", "ـ": ""})
# نشانه‌های جهت‌دهی نامرئی و BOM (نیم‌فاصله U+200C عمداً حفظ می‌شود)
_INVISIBLE = re.compile("[\u200e\u200f\u202a-\u202e\u2066-\u2069\ufeff\u00ad]")


class ExtractionError(Exception):
    """متن فایل قابل‌استخراج نیست؛ پیام فارسی و قابل‌نمایش به کاربر است."""


def normalize(text: str) -> str:
    """یکدست‌سازی: حروف عربی ← فارسی، حذف کشیده و نشانه‌های نامرئی، جمع‌کردن فاصله‌ها."""
    text = unicodedata.normalize("NFKC", text or "")      # شکل‌های نمایشی حروف ← حرف پایه
    text = _INVISIBLE.sub("", text).translate(_ARABIC_TO_PERSIAN)
    lines = [re.sub(r"[ \t\u00a0]+", " ", line).strip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


# عدد فارسی/عربی، همراه با جداکننده‌های میان ارقام: ۱۴۰۴/۹/۱ ، ۵-۲ ، ۱۸٫۵
_PERSIAN_NUMBER = re.compile(r"[۰-۹٠-٩]+(?:[/.\-:٫٬][۰-۹٠-٩]+)*")
_RTL_LETTER = re.compile(r"[\u0600-\u06ef\u06fa-\u06ff]")
_SWAP_PARENS = str.maketrans("()", ")(")


def _repair_rtl(text: str) -> str:
    """
    دو خطای شناخته‌شده‌ی استخراج متن راست‌به‌چپ از PDF را برمی‌گرداند:
      ۱. pypdf ارقام فارسی را هم مثل حروف «از راست به چپ» می‌چیند، پس هر عدد وارونه درمی‌آید
         (۱۴۰۴ ← ۴۰۴۱)؛ برای مهلت‌ها و شماره‌ی بندها این خطا مهم است.
      ۲. پرانتزها در متن راست‌به‌چپ آینه‌ای ذخیره می‌شوند: «)ایمیل(».
    """
    text = _PERSIAN_NUMBER.sub(lambda match: match.group()[::-1], text)
    lines = []
    for line in text.splitlines():
        opening, closing = line.find("("), line.find(")")
        if 0 <= closing and (opening < 0 or closing < opening) and _RTL_LETTER.search(line):
            line = line.translate(_SWAP_PARENS)
        lines.append(line)
    return "\n".join(lines)


def _pdf(data: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        raise ExtractionError("فایل PDF رمزگذاری شده است و قابل‌خواندن نیست.")
    return _repair_rtl("\n\n".join(page.extract_text() or "" for page in reader.pages))


def _docx(data: bytes) -> str:
    from docx import Document
    from docx.table import Table
    parts = []
    for block in Document(io.BytesIO(data)).iter_inner_content():   # پاراگراف‌ها و جدول‌ها به ترتیب سند
        if isinstance(block, Table):
            for row in block.rows:
                cells = []
                for cell in row.cells:                               # خانه‌های ادغام‌شده تکراری برمی‌گردند
                    value = cell.text.strip()
                    if value and (not cells or cells[-1] != value):
                        cells.append(value)
                if cells:
                    parts.append(" | ".join(cells))
        else:
            parts.append(block.text)
    return "\n".join(parts)


def _read(file) -> bytes:
    if isinstance(file, (str, Path)):
        return Path(file).read_bytes()
    file.open("rb")                 # FieldFile جنگو (مثل DocumentVersion.file)
    try:
        return file.read()
    finally:
        file.close()


def extract_text(file, name=None) -> str:
    """
    file: مسیر فایل، یا FieldFile جنگو. خروجی: متن یکدست‌شده.
    برای قالب پشتیبانی‌نشده یا فایل خراب ExtractionError می‌دهد.
    """
    extension = Path(str(name or getattr(file, "name", file))).suffix.lower()
    if extension not in (".pdf", ".docx", ".txt", ".md"):
        raise ExtractionError("فقط فایل‌های PDF و DOCX قابل بررسی خودکارند "
                              "(قالب قدیمی doc را در Word به‌صورت PDF یا DOCX ذخیره کنید).")
    try:
        data = _read(file)
        if extension == ".pdf":
            text = _pdf(data)
        elif extension == ".docx":
            text = _docx(data)
        else:
            text = data.decode("utf-8", errors="replace")
    except ExtractionError:
        raise
    except Exception as e:          # فایل خراب، PDF ناقص و …
        raise ExtractionError("متن این فایل قابل‌استخراج نبود (شاید فایل خراب باشد).") from e
    return normalize(text)
