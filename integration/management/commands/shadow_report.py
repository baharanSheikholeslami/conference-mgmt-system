from pathlib import Path

from django.core.management.base import BaseCommand

from integration import shadow


class Command(BaseCommand):
    help = ("گزارش حالت سایه: پیشنهادهای عامل‌ها (داور، استاد راهنما، بررسی قالب) را با تصمیم واقعی "
            "انسان‌ها مقایسه می‌کند و آمار اعلان‌ها و دستیار را می‌دهد. فقط می‌خواند و چیزی را تغییر نمی‌دهد.")

    def add_arguments(self, parser):
        parser.add_argument("--include-scenarios", action="store_true",
                            help="پرونده‌های سناریوهای آزمایشی (e2e_*) را هم حساب کن")
        parser.add_argument("--output", metavar="فایل", help="گزارش را در این فایل Markdown ذخیره کن")

    def handle(self, *args, **options):
        text = shadow.markdown(options["include_scenarios"])
        if options["output"]:
            Path(options["output"]).parent.mkdir(parents=True, exist_ok=True)
            Path(options["output"]).write_text(text, encoding="utf-8")
            self.stdout.write(f"گزارش در {options['output']} ذخیره شد.")
        else:
            self.stdout.write(text)
