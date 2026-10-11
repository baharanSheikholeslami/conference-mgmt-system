from datetime import date
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from integration import report
from integration.runner import Runner, cleanup
from integration.scenarios import BY_KEY, SCENARIOS


class Command(BaseCommand):
    help = ("سناریوهای مصنوعی سرتاسری (از انتخاب استاد راهنما تا ثبت نمره) را با ساعت شبیه‌سازی‌شده اجرا "
            "و گزارش می‌کند. پیش‌فرض آفلاین است و هیچ داده‌ای باقی نمی‌گذارد؛ با --live پورتال واقعاً با "
            "n8n و مدل زبانی کار می‌کند و خروجی عامل‌ها هم سنجیده می‌شود.")

    def add_arguments(self, parser):
        parser.add_argument("scenario", nargs="*", help=f"فقط این سناریوها: {', '.join(BY_KEY)}")
        parser.add_argument("--live", action="store_true",
                            help="اجرای زنده با n8n و مدل زبانی (سرور جنگو و n8n باید روشن باشند)")
        parser.add_argument("--keep", action="store_true",
                            help="پرونده‌های سناریو را نگه دار تا در پورتال دیده شوند (کاربران e2e_*)")
        parser.add_argument("--cleanup", action="store_true", help="فقط پرونده‌های سناریوهای قبلی را پاک کن")
        parser.add_argument("--start", help="روز شروع شبیه‌سازی به شکل YYYY-MM-DD (پیش‌فرض: امروز)")
        parser.add_argument("--agent-timeout", type=int, default=300,
                            help="حداکثر ثانیه‌ی انتظار برای پاسخ هر عامل در اجرای زنده (پیش‌فرض ۳۰۰)")
        parser.add_argument("--report", metavar="پوشه",
                            help="گزارش Markdown و JSON را در این پوشه ذخیره کن (مثلاً docs/reports)")
        parser.add_argument("--list", action="store_true", help="فهرست سناریوها")

    def handle(self, *args, **o):
        if o["list"]:
            for s in SCENARIOS:
                self.stdout.write(f"{s.key:20} {s.title} ({len(s.steps)} گام)")
            return
        if o["cleanup"]:
            self.stdout.write(f"{cleanup()} پرونده‌ی سناریو پاک شد.")
            return
        unknown = [k for k in o["scenario"] if k not in BY_KEY]
        if unknown:
            raise CommandError(f"سناریوی ناشناخته: {', '.join(unknown)}")
        if o["live"] and not settings.AGENTS_ENABLED:
            raise CommandError("برای اجرای زنده AGENTS_ENABLED=True را در .env بگذارید.")
        try:
            start = date.fromisoformat(o["start"]) if o["start"] else timezone.localdate()
        except ValueError:
            raise CommandError("--start باید به شکل YYYY-MM-DD باشد.") from None

        scenarios = [BY_KEY[k] for k in o["scenario"]] or SCENARIOS
        runner = Runner(live=o["live"], start=start, agent_timeout=o["agent_timeout"],
                        persist=o["keep"], log=self.stdout.write)
        results = runner.run(scenarios)
        if o["live"] and not o["keep"]:
            cleanup()

        text = report.markdown(results, o["live"], start)
        if o["report"]:
            folder = Path(o["report"])
            folder.mkdir(parents=True, exist_ok=True)
            stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
            name = f"e2e-{'live' if o['live'] else 'offline'}-{stamp}"
            (folder / f"{name}.md").write_text(text, encoding="utf-8")
            (folder / f"{name}.json").write_text(report.as_json(results, o["live"], start), encoding="utf-8")
            self.stdout.write(f"\nگزارش: {folder / name}.md")

        ok = sum(r.ok for r in results)
        style = self.style.SUCCESS if ok == len(results) else self.style.ERROR
        self.stdout.write(style(f"\n{ok} از {len(results)} سناریو بدون خطا اجرا شد."))
        if o["keep"]:
            self.stdout.write("پرونده‌ها نگه داشته شدند (کاربران e2e_* بدون رمز؛ با ادمین ببینید). "
                              "پاک‌کردن: python manage.py run_scenarios --cleanup")
        if ok != len(results):
            raise CommandError("دست‌کم یک سناریو شکست خورد.")
