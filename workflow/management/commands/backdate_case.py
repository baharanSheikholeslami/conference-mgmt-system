from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from workflow.models import ProjectCase

DATE_FIELDS = ("registration_date", "edu_received_at", "final_approved_at",
               "thesis_submitted_at", "defense_date")


class Command(BaseCommand):
    help = ("فقط برای آزمایش دستی: همه‌ی تاریخ‌های یک پرونده را چند روز به گذشته می‌برد، تا بشود بدون "
            "سه ماه انتظار، دفاع را زمان‌بندی کرد و نمره را در همان روز ثبت کرد. فقط با DEBUG=True کار می‌کند.")

    def add_arguments(self, parser):
        parser.add_argument("case_id", type=int, help="شماره‌ی پرونده (در نشانی صفحه‌ی پرونده: /cases/<شماره>/)")
        parser.add_argument("--days", type=int, default=120, help="چند روز به گذشته (پیش‌فرض ۱۲۰)")

    @transaction.atomic
    def handle(self, *args, case_id, days, **options):
        if not settings.DEBUG:
            raise CommandError("این دستور فقط برای محیط آزمایشی است (DEBUG=True).")
        try:
            case = ProjectCase.objects.get(pk=case_id)
        except ProjectCase.DoesNotExist:
            raise CommandError(f"پرونده‌ای با شماره‌ی {case_id} نیست.") from None
        shift = timedelta(days=days)
        for field in DATE_FIELDS:
            if getattr(case, field):
                setattr(case, field, getattr(case, field) - shift)
        case.save()
        case.sync_deadlines()
        for deadline in case.deadlines.exclude(satisfied_at__isnull=True):
            deadline.satisfied_at -= shift
            deadline.save(update_fields=["satisfied_at"])
        self.stdout.write(self.style.SUCCESS(
            f"پرونده‌ی {case_id} ({case.get_state_display()}) {days} روز به گذشته رفت."))
