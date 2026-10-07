from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Count

from agents import n8n, services
from agents.models import PolicyChunk


class Command(BaseCommand):
    help = "اتصال پورتال ↔ n8n ↔ مدل زبانی را گام‌به‌گام می‌آزماید و می‌گوید مشکل کجاست."

    def add_arguments(self, parser):
        parser.add_argument("--ask", metavar="پرسش", help="یک پرسش آزمایشی هم از دستیار RAG بپرس")

    def ok(self, text):
        self.stdout.write(self.style.SUCCESS("  ✓ ") + text)

    def fail(self, text, hint=""):
        self.failed = True
        self.stdout.write(self.style.ERROR("  ✗ ") + text + (f"\n      ↳ {hint}" if hint else ""))

    def handle(self, *args, **options):
        self.failed = False
        self.stdout.write("۱) تنظیمات پورتال (.env)")
        if settings.AGENTS_ENABLED:
            self.ok("AGENTS_ENABLED=True")
        else:
            self.fail("AGENTS_ENABLED خاموش است", "در .env بنویسید AGENTS_ENABLED=True و سرور را دوباره اجرا کنید.")
        if len(settings.AGENT_TOKEN) >= 16:
            self.ok("AGENT_TOKEN تنظیم شده است")
        else:
            self.fail("AGENT_TOKEN خالی یا کوتاه است", "یک رشته‌ی تصادفی بلند در .env بگذارید.")

        self.stdout.write(f"۲) n8n در {settings.N8N_WEBHOOK_URL}")
        if not n8n.healthy():
            self.fail("n8n پاسخ نمی‌دهد", "docker compose up -d را اجرا کنید و http://localhost:5678 را باز کنید.")
            return self.summary()
        self.ok("n8n بالا است")

        self.stdout.write("۳) گردش‌کار rag-embed و مدل embedding")
        model = ""
        try:
            vectors, model = services.embed(["آزمون اتصال"])
            self.ok(f"بردار {len(vectors[0])}بعدی با مدل «{model}» دریافت شد")
        except n8n.Unavailable as e:
            self.fail(str(e), "گردش‌کارها را در n8n وارد و فعال (Active/Publish) کرده‌اید؟ "
                              "اگر بله، خطای دقیق را در n8n ← Executions ببینید (مدل embedding در دسترس است؟).")

        self.stdout.write("۴) پایگاه دانش برداری")
        by_model = dict(PolicyChunk.objects.values_list("embedding_model").annotate(n=Count("id")))
        if not by_model:
            self.fail("هیچ سندی نمایه نشده است", "python manage.py index_policy")
        elif model and model not in by_model:
            self.fail(f"اسناد با مدل دیگری نمایه شده‌اند ({', '.join(by_model)})",
                      "پس از عوض‌کردن EMBEDDING_MODEL باید index_policy را دوباره اجرا کنید.")
        else:
            self.ok(f"{sum(by_model.values())} قطعه نمایه شده است")

        if options["ask"]:
            self.stdout.write("۵) پرسش آزمایشی از دستیار (rag-ask و مدل زبانی)")
            try:
                result = services.ask_assistant(None, options["ask"])
                self.ok("پاسخ دریافت شد:")
                self.stdout.write(f"\n{result['answer']}\n")
                for source in result["sources"]:
                    mark = "*" if source["cited"] else " "
                    self.stdout.write(f"   {mark}[{source['n']}] {source['doc']} — {source['ref']}")
            except services.AgentError as e:
                self.fail(str(e), "خطای دقیق را در n8n ← Executions ببینید (مدل گفتگو در دسترس است؟).")
        self.summary()

    def summary(self):
        if self.failed:
            self.stdout.write(self.style.ERROR("\nدست‌کم یک مورد نیاز به رسیدگی دارد."))
        else:
            self.stdout.write(self.style.SUCCESS("\nهمه‌چیز آماده است."))
