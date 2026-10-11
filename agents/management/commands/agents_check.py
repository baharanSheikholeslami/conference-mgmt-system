import json
import os
import urllib.error
import urllib.request

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Count
from django.urls import reverse

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

        if settings.AGENTS_SHADOW_MODE:
            self.ok(f"حالت سایه روشن است: خروجی عامل‌ها فقط برای آموزش، ایمیل‌ها به {settings.AGENTS_SHADOW_MAILBOX}")

        self.stdout.write(f"۲) n8n در {settings.N8N_WEBHOOK_URL}")
        if not n8n.healthy():
            self.fail("n8n پاسخ نمی‌دهد", "docker compose up -d را اجرا کنید و http://localhost:5678 را باز کنید.")
            return self.summary()
        self.ok("n8n بالا است")
        self.check_portal()
        self.check_ollama()

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

    def check_portal(self):
        """n8n نتیجه‌ی عامل‌ها را به API پورتال برمی‌گرداند؛ اگر سرور جنگو خاموش باشد همه‌ی عامل‌ها «بی‌پاسخ» می‌مانند."""
        url = settings.PORTAL_PUBLIC_URL + reverse("login")
        try:
            with urllib.request.urlopen(url, timeout=3):
                self.ok(f"پورتال در {settings.PORTAL_PUBLIC_URL} پاسخ می‌دهد (n8n نتیجه‌ها را به آن برمی‌گرداند)")
        except (urllib.error.URLError, TimeoutError, OSError):
            self.fail(f"پورتال در {settings.PORTAL_PUBLIC_URL} پاسخ نمی‌دهد",
                      "در ترمینال دیگری اجرا کنید: python manage.py runserver 0.0.0.0:8000")

    def check_ollama(self):
        """اگر مدل‌ها روی Ollama همین مک هستند، بررسی می‌کند هر دو مدل دانلود شده باشند."""
        base = os.getenv("LLM_BASE_URL", "")
        if ":11434" not in base:
            return
        wanted = {os.getenv("LLM_CHAT_MODEL", "gemma3:4b"), os.getenv("EMBEDDING_MODEL", "bge-m3")}
        try:
            with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3) as response:
                installed = {m["name"] for m in json.load(response).get("models", [])}
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            self.fail("Ollama روی این مک در حال اجرا نیست", "برنامه‌ی Ollama را باز کنید یا: ollama serve")
            return
        installed |= {name.removesuffix(":latest") for name in installed}
        missing = sorted(m for m in wanted if m not in installed)
        if missing:
            self.fail(f"مدل(های) {', '.join(missing)} در Ollama نیست",
                      " ؛ ".join(f"ollama pull {m}" for m in missing))
        else:
            self.ok(f"Ollama مدل‌های {', '.join(sorted(wanted))} را دارد")

    def summary(self):
        if self.failed:
            self.stdout.write(self.style.ERROR("\nدست‌کم یک مورد نیاز به رسیدگی دارد."))
        else:
            self.stdout.write(self.style.SUCCESS("\nهمه‌چیز آماده است."))
