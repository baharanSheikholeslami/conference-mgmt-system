from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from agents import n8n, services


class Command(BaseCommand):
    help = ("یادآوری‌های مهلت را همین حالا اجرا می‌کند (همان کاری که n8n هر روز ساعت ۸ خودش انجام می‌دهد). "
            "با --today یا --days-ahead می‌شود روز دیگری را شبیه‌سازی کرد.")

    def add_arguments(self, parser):
        parser.add_argument("--today", help="تاریخ میلادی شبیه‌سازی‌شده به شکل YYYY-MM-DD")
        parser.add_argument("--days-ahead", type=int, metavar="N",
                            help="شبیه‌سازی N روز بعد از امروز (مثلاً ۵۴)")
        parser.add_argument("--dry-run", action="store_true",
                            help="فقط فهرست یادآوری‌های سررسیدشده را چاپ کن؛ ایمیلی فرستاده نمی‌شود")

    def handle(self, *args, **options):
        today = None
        try:
            if options["today"]:
                today = date.fromisoformat(options["today"])
            elif options["days_ahead"] is not None:
                today = timezone.localdate() + timedelta(days=options["days_ahead"])
        except ValueError:
            raise CommandError("--today باید به شکل YYYY-MM-DD باشد.") from None
        if today:
            self.stdout.write(f"تاریخ شبیه‌سازی‌شده: {today.isoformat()}")
        if options["dry_run"]:
            due = services.due_reminders(today)
            for item in due:
                self.stdout.write(f"{item['key']}  →  {item['to']}\n    {item['subject']}")
            self.stdout.write(self.style.SUCCESS(f"{len(due)} یادآوری سررسید شده است."))
            return
        try:
            n8n.post("run-reminders", {"today": today.isoformat() if today else ""})
        except n8n.Unavailable as e:
            raise CommandError(str(e)) from e
        self.stdout.write(self.style.SUCCESS(
            "گردش‌کار یادآوری در n8n آغاز شد؛ ایمیل‌ها را در Mailpit (http://localhost:8025) ببینید."))
