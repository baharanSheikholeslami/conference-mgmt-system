from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count

from agents import chunking, extract, services
from agents.models import PolicyChunk

KNOWLEDGE_DIR = Path(__file__).resolve().parents[2] / "knowledge"
EXTENSIONS = (".pdf", ".docx", ".md", ".txt")


class Command(BaseCommand):
    help = ("نمایه‌سازی اسناد رسمی در پایگاه دانش برداری دستیار (RAG). بدون ورودی، همه‌ی فایل‌های "
            "پوشه‌ی agents/knowledge نمایه می‌شوند. چندبار اجرا کردن مشکلی ندارد (جایگزین می‌شود).")

    def add_arguments(self, parser):
        parser.add_argument("path", nargs="?", help="مسیر یک فایل PDF/DOCX/MD/TXT")
        parser.add_argument("--code", help="کد سند، مثلاً AUT-PR-3210 (پیش‌فرض: نام فایل)")
        parser.add_argument("--title", default="", help="عنوان سند")
        parser.add_argument("--dry-run", action="store_true",
                            help="فقط قطعه‌بندی را نشان بده؛ چیزی ذخیره نمی‌شود و به n8n نیازی نیست")
        parser.add_argument("--list", action="store_true", help="اسناد نمایه‌شده را نشان بده")

    def handle(self, *args, **options):
        if options["list"]:
            return self.show_index()
        if options["path"]:
            path = Path(options["path"]).expanduser()
            if not path.is_file():
                raise CommandError(f"فایل پیدا نشد: {path}")
            files = [(path, options["code"] or path.stem, options["title"])]
        else:
            files = [(p, p.stem, self.first_heading(p)) for p in sorted(KNOWLEDGE_DIR.iterdir())
                     if p.suffix.lower() in EXTENSIONS]
            if not files:
                raise CommandError(f"فایلی در {KNOWLEDGE_DIR} نیست.")
        for path, code, title in files:
            if options["dry_run"]:
                self.preview(path, code)
            else:
                self.index(path, code, title)

    @staticmethod
    def first_heading(path):
        """عنوان سند = نخستین سرخط «# …» فایل‌های Markdown."""
        if path.suffix.lower() != ".md":
            return ""
        lines = path.read_text(encoding="utf-8").splitlines()
        return next((line[2:].strip() for line in lines if line.startswith("# ")), "")

    def preview(self, path, code):
        try:
            chunks = chunking.split(extract.extract_text(path))
        except extract.ExtractionError as e:
            raise CommandError(str(e)) from e
        self.stdout.write(self.style.MIGRATE_HEADING(f"{code}: {len(chunks)} قطعه"))
        for i, chunk in enumerate(chunks, 1):
            self.stdout.write(f"  [{i}] ({len(chunk.text)} نویسه) {chunk.ref or '—'}\n      {chunk.text[:110]}…")

    def index(self, path, code, title):
        if not settings.AGENTS_ENABLED:
            raise CommandError("AGENTS_ENABLED=True را در .env بگذارید (نمایه‌سازی به n8n نیاز دارد).")
        self.stdout.write(f"نمایه‌سازی {path.name} با کد «{code}» …")
        try:
            count, model = services.index_document(path, code, title)
        except services.AgentError as e:
            raise CommandError(str(e)) from e
        self.stdout.write(self.style.SUCCESS(f"  {count} قطعه با مدل «{model}» نمایه شد."))

    def show_index(self):
        rows = (PolicyChunk.objects.values("doc_code", "embedding_model")
                .annotate(n=Count("id")).order_by("doc_code"))
        if not rows:
            self.stdout.write("پایگاه دانش خالی است.")
        for row in rows:
            self.stdout.write(f"{row['doc_code']}: {row['n']} قطعه — مدل {row['embedding_model']}")
