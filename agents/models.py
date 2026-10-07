"""
داده‌های لایه‌ی عامل‌ها (فاز ۳).

اصل انسان‌در‌حلقه: هیچ‌کدام از این جدول‌ها وضعیت پرونده را تغییر نمی‌دهند. این‌جا فقط
«خروجی عامل‌ها» نگهداری می‌شود (بازخورد قالب، فهرست پیشنهادی داور، پاسخ دستیار، اعلان‌ها)
تا در پورتال به انسان نمایش داده شود؛ تصمیم همچنان با perform() و اقدام کاربر است.
"""
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from pgvector.django import VectorField

from workflow.models import CaseEvent, Deadline, DocumentVersion, ProjectCase

# اگر n8n پس از این مدت پاسخی برنگرداند، بررسی «بی‌پاسخ» حساب می‌شود (در صفحه دکمه‌ی تلاش دوباره می‌آید)
STALE_AFTER = timedelta(minutes=10)


class _AgentRun(models.Model):
    """فیلدهای مشترک هر درخواست ناهم‌زمان به n8n."""
    error = models.CharField("خطا", max_length=300, blank=True)
    model = models.CharField("مدل", max_length=120, blank=True)
    requested_at = models.DateTimeField("زمان درخواست", default=timezone.now)
    finished_at = models.DateTimeField("زمان پاسخ", null=True, blank=True)

    class Meta:
        abstract = True

    PENDING = "pending"

    @property
    def is_pending(self):
        return self.status == self.PENDING

    @property
    def is_stale(self):
        return self.is_pending and timezone.now() - self.requested_at > STALE_AFTER

    @property
    def is_waiting(self):
        """هنوز منتظر پاسخ n8n هستیم (صفحه خودکار تازه می‌شود)."""
        return self.is_pending and not self.is_stale


# ───────────────────────── ۱. اعتبارسنجی سند ─────────────────────────

class DocumentValidation(_AgentRun):
    """نتیجه‌ی بررسی قالب یک نسخه‌ی پیشنهاد. فقط بازخورد است و مانع هیچ اقدامی نمی‌شود."""

    class Status(models.TextChoices):
        PENDING = "pending", "در حال بررسی"
        PASSED = "passed", "مطابق قالب"
        ISSUES = "issues", "نیازمند توجه"
        ERROR = "error", "بررسی انجام نشد"

    version = models.OneToOneField(DocumentVersion, on_delete=models.CASCADE, related_name="validation")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    # [{"key", "label", "group", "ok", "note"}] — به ترتیب چک‌لیست
    items = models.JSONField(default=list, blank=True)

    class Meta:
        verbose_name = "اعتبارسنجی سند"
        verbose_name_plural = "اعتبارسنجی‌های سند"

    def __str__(self):
        return f"{self.version} — {self.get_status_display()}"

    @property
    def problems(self):
        return [i for i in self.items if not i.get("ok")]


# ───────────────────────── ۲. دستیار RAG ─────────────────────────

class PolicyChunk(models.Model):
    """یک قطعه از سند رویه به‌همراه بردار معنایی آن (پایگاه دانش برداری در pgvector)."""
    doc_code = models.CharField("کد سند", max_length=60, db_index=True)
    doc_title = models.CharField("عنوان سند", max_length=250, blank=True)
    position = models.PositiveIntegerField("ترتیب")
    ref = models.CharField("بند مرجع", max_length=200, blank=True)
    text = models.TextField("متن")
    # بدون ابعاد ثابت تا با عوض‌شدن مدل embedding نیازی به مایگریشن نباشد؛ جست‌وجو همیشه
    # فقط میان قطعه‌هایی است که با همان مدل نمایه شده‌اند (embedding_model).
    embedding = VectorField()
    embedding_model = models.CharField("مدل embedding", max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["doc_code", "position"]
        verbose_name = "قطعه‌ی پایگاه دانش"
        verbose_name_plural = "قطعه‌های پایگاه دانش"
        constraints = [models.UniqueConstraint(fields=["doc_code", "position"],
                                               name="unique_chunk_position")]

    def __str__(self):
        return f"{self.doc_code} #{self.position} — {self.ref}"


class AssistantMessage(models.Model):
    """سابقه‌ی پرسش و پاسخ دستیار؛ داده‌ی خام ارزیابی سطح ۳ (فاز ۵)."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name="+")
    question = models.TextField("پرسش")
    answer = models.TextField("پاسخ", blank=True)
    sources = models.JSONField("بندهای بازیابی‌شده", default=list, blank=True)
    ok = models.BooleanField("موفق", default=True)
    error = models.CharField(max_length=300, blank=True)
    model = models.CharField(max_length=120, blank=True)
    latency_ms = models.PositiveIntegerField("زمان پاسخ (میلی‌ثانیه)", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "پرسش از دستیار"
        verbose_name_plural = "پرسش‌های دستیار"

    def __str__(self):
        return self.question[:60]


# ───────────────────────── ۳. پیشنهاد داور ─────────────────────────

class SuggestionRun(_AgentRun):
    """آخرین درخواست «پیشنهاد داور» برای یک پرونده."""

    class Status(models.TextChoices):
        PENDING = "pending", "در حال آماده‌سازی"
        DONE = "done", "آماده"
        ERROR = "error", "انجام نشد"

    case = models.OneToOneField(ProjectCase, on_delete=models.CASCADE, related_name="suggestion_run")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)

    class Meta:
        verbose_name = "درخواست پیشنهاد داور"
        verbose_name_plural = "درخواست‌های پیشنهاد داور"

    def __str__(self):
        return f"{self.case} — {self.get_status_display()}"


class RefereeSuggestion(models.Model):
    run = models.ForeignKey(SuggestionRun, on_delete=models.CASCADE, related_name="suggestions")
    referee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    rank = models.PositiveSmallIntegerField("رتبه")
    score = models.FloatField("شباهت معنایی", null=True, blank=True,
                              help_text="شباهت کسینوسی موضوع پیشنهاد با تخصص استاد؛ خالی = تخصص ثبت نشده")
    reason = models.TextField("توجیه", blank=True)

    class Meta:
        ordering = ["rank"]
        verbose_name = "داور پیشنهادی"
        verbose_name_plural = "داوران پیشنهادی"
        constraints = [models.UniqueConstraint(fields=["run", "referee"], name="one_row_per_referee")]

    @property
    def percent(self):
        return None if self.score is None else round(max(0.0, min(1.0, self.score)) * 100)


# ───────────────────────── ۴. اطلاع‌رسانی ─────────────────────────

class Notification(models.Model):
    """
    سابقه‌ی اعلان‌های یک‌طرفه‌ی ایمیلی. دو نوع دارد:
      • event    — به‌دنبال تغییر وضعیت پرونده (یک ردیف برای هر گیرنده‌ی هر رویداد)
      • reminder — یادآوری مهلت (یک ردیف برای هر گیرنده‌ی هر «مرحله‌ی یادآوری» هر مهلت)
    قیدهای یکتایی تضمین می‌کنند هیچ اعلانی دو بار ثبت نشود.
    """

    class Kind(models.TextChoices):
        EVENT = "event", "تغییر وضعیت"
        REMINDER = "reminder", "یادآوری مهلت"

    class Status(models.TextChoices):
        QUEUED = "queued", "در صف ارسال"
        SENT = "sent", "ارسال شد"
        FAILED = "failed", "ناموفق"

    case = models.ForeignKey(ProjectCase, on_delete=models.CASCADE, related_name="notifications")
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                  related_name="notifications", verbose_name="گیرنده")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    event = models.ForeignKey(CaseEvent, null=True, blank=True, on_delete=models.CASCADE,
                              related_name="notifications")
    deadline = models.ForeignKey(Deadline, null=True, blank=True, on_delete=models.CASCADE,
                                 related_name="notifications")
    stage = models.CharField("مرحله‌ی یادآوری", max_length=20, blank=True)
    subject = models.CharField("موضوع", max_length=250)
    body = models.TextField("متن", blank=True)
    generated_by = models.CharField("تولیدکننده‌ی متن", max_length=20, blank=True,
                                    help_text="llm = مدل زبانی، template = متن ثابت پشتیبان")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "اعلان"
        verbose_name_plural = "اعلان‌ها"
        constraints = [
            models.UniqueConstraint(fields=["event", "recipient"], condition=Q(event__isnull=False),
                                    name="one_event_notice_per_recipient"),
            models.UniqueConstraint(fields=["deadline", "stage", "recipient"],
                                    condition=Q(deadline__isnull=False),
                                    name="one_reminder_per_stage_per_recipient"),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} → {self.recipient}: {self.subject[:50]}"
