from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from . import rules
from .enums import (CaseRole, DeadlineKind, DocKind, ReviewDecision, Semester,
                    State, UserType)


# ───────────────────────── کاربران ─────────────────────────

class Department(models.Model):
    name = models.CharField("نام گروه آموزشی", max_length=120, unique=True)

    class Meta:
        verbose_name = "گروه آموزشی"
        verbose_name_plural = "گروه‌های آموزشی"

    def __str__(self):
        return self.name


class User(AbstractUser):
    """
    نوع حساب (user_type) ویژگی خود کاربر است؛ اما «استاد راهنما / داور / مدیر گروه»
    بودن نسبت به هر پرونده تعیین می‌شود (ProjectCase.roles_of).
    یک عضو هیئت‌علمی می‌تواند هم‌زمان راهنمای یک پرونده و داور پرونده‌ی دیگر باشد.
    """
    user_type = models.CharField("نوع حساب", max_length=10, choices=UserType.choices,
                                 default=UserType.STUDENT)
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="members", verbose_name="گروه آموزشی")
    is_department_head = models.BooleanField("مدیر گروه", default=False)
    student_number = models.CharField("شماره دانشجویی", max_length=20, blank=True)
    expertise = models.TextField("زمینه‌های تخصصی", blank=True,
                                 help_text="متن آزاد؛ در فاز ۳ برای تطبیق معنایی (embedding) استفاده می‌شود")

    class Meta:
        verbose_name = "کاربر"
        verbose_name_plural = "کاربران"
        constraints = [
            models.UniqueConstraint(fields=["department"], condition=Q(is_department_head=True),
                                    name="one_head_per_department"),
        ]

    def __str__(self):
        return self.get_full_name() or self.username


# ───────────────────────── پرونده ─────────────────────────

class ProjectCase(models.Model):
    author = models.ForeignKey(User, on_delete=models.PROTECT, related_name="authored_cases",
                               verbose_name="دانشجو / نویسنده")
    advisor = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT,
                                related_name="advised_cases", verbose_name="استاد راهنما")
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.PROTECT,
                                   verbose_name="گروه آموزشی")
    title_fa = models.CharField("عنوان فارسی", max_length=250)
    title_en = models.CharField("عنوان انگلیسی", max_length=250, blank=True)

    academic_year = models.PositiveSmallIntegerField("سال تحصیلی (شمسی، مثلاً ۱۴۰۴)")
    semester = models.CharField("نیم‌سال ثبت‌نام", max_length=10, choices=Semester.choices)
    registration_date = models.DateField("تاریخ ثبت‌نام پروژه")

    state = models.CharField("وضعیت", max_length=30, choices=State.choices,
                             default=State.ADVISOR_SELECTION, db_index=True)

    # تاریخ‌های رویدادی (مبنای محاسبه‌ی مهلت‌ها)
    edu_received_at = models.DateField("تاریخ دریافت در آموزش دانشکده", null=True, blank=True)
    final_approved_at = models.DateField("تاریخ تصویب نهایی", null=True, blank=True)
    thesis_submitted_at = models.DateField("تاریخ تحویل پایان‌نامه", null=True, blank=True)
    defense_date = models.DateField("تاریخ دفاع", null=True, blank=True)
    defense_place = models.CharField("محل دفاع", max_length=200, blank=True)
    grade = models.DecimalField("نمره", max_digits=4, decimal_places=2, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "پرونده‌ی پروژه"
        verbose_name_plural = "پرونده‌های پروژه"
        constraints = [
            models.CheckConstraint(
                condition=Q(state=State.ADVISOR_SELECTION)
                | (Q(advisor__isnull=False) & Q(department__isnull=False)),
                name="advisor_required_after_selection"),
            models.CheckConstraint(
                condition=Q(grade__isnull=True) | (Q(grade__gte=0) & Q(grade__lte=20)),
                name="grade_between_0_and_20"),
            models.UniqueConstraint(
                fields=["author", "academic_year", "semester"],
                condition=~Q(state__in=[State.REJECTED, State.CANCELLED]),
                name="one_active_case_per_author_term"),
        ]

    def __str__(self):
        return self.title_fa

    # ── قواعد وابسته به نوع نویسنده ──
    @property
    def is_graded(self):
        """نویسنده غیردانشجو مشمول برنامه‌ی نمره‌دهی نیست."""
        return self.author.user_type == UserType.STUDENT

    @property
    def required_referees(self):
        return rules.REFEREES_FOR_STUDENT if self.is_graded else rules.REFEREES_FOR_NON_STUDENT

    @property
    def is_terminal(self):
        return self.state in (State.COMPLETED, State.REJECTED, State.CANCELLED)

    # ── نقش‌های زمینه‌ای ──
    def roles_of(self, user):
        roles = set()
        if user.id == self.author_id:
            roles.add(CaseRole.AUTHOR)
        if self.advisor_id and user.id == self.advisor_id:
            roles.add(CaseRole.ADVISOR)
        if (user.is_department_head and self.department_id
                and user.department_id == self.department_id):
            roles.add(CaseRole.HEAD)
        if user.user_type == UserType.EDU_STAFF:
            roles.add(CaseRole.EDU)
        if self.pk and self.referee_assignments.filter(referee=user).exists():
            roles.add(CaseRole.REFEREE)
        return roles

    # ── مهلت‌ها ──
    def expected_deadlines(self):
        """مهلت‌هایی که با اطلاعات فعلی پرونده قابل‌محاسبه‌اند: {DeadlineKind: date}."""
        d = {DeadlineKind.PROPOSAL_SUBMISSION:
             rules.add_months(self.registration_date, rules.PROPOSAL_SUBMISSION_MONTHS)}
        if self.edu_received_at:
            d[DeadlineKind.GROUP_REVIEW] = rules.add_months(self.edu_received_at,
                                                            rules.GROUP_REVIEW_MONTHS)
        if self.final_approved_at:
            d[DeadlineKind.DEFENSE_EARLIEST] = rules.add_months(
                self.final_approved_at, rules.MIN_MONTHS_APPROVAL_TO_DEFENSE)
        if self.is_graded:
            d[DeadlineKind.THESIS_DELIVERY] = rules.thesis_delivery_deadline(
                self.academic_year, self.semester)
            d[DeadlineKind.GRADE_ENTRY] = rules.grade_entry_deadline(
                self.academic_year, self.semester)
        return d

    def sync_deadlines(self):
        """ردیف‌های Deadline را با مقادیر محاسبه‌شده هماهنگ می‌کند (در فاز ۲ پس از هر گذار فراخوانی می‌شود)."""
        for kind, due in self.expected_deadlines().items():
            Deadline.objects.update_or_create(case=self, kind=kind, defaults={"due_date": due})


class RefereeAssignment(models.Model):
    case = models.ForeignKey(ProjectCase, on_delete=models.CASCADE, related_name="referee_assignments")
    referee = models.ForeignKey(User, on_delete=models.PROTECT, related_name="referee_assignments",
                                verbose_name="داور")
    assigned_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "تخصیص داور"
        verbose_name_plural = "تخصیص‌های داور"
        constraints = [models.UniqueConstraint(fields=["case", "referee"], name="unique_referee_per_case")]

    def clean(self):
        if self.referee.user_type != UserType.FACULTY:
            raise ValidationError("داور باید عضو هیئت‌علمی باشد.")
        if self.referee.department_id != self.case.department_id:
            raise ValidationError("داور باید عضو گروه آموزشی پرونده باشد.")
        if self.referee_id in (self.case.advisor_id, self.case.author_id):
            raise ValidationError("استاد راهنما یا نویسنده نمی‌تواند داور همان پرونده باشد.")

    def __str__(self):
        return f"{self.referee} ← {self.case}"


class DocumentVersion(models.Model):
    case = models.ForeignKey(ProjectCase, on_delete=models.CASCADE, related_name="versions")
    kind = models.CharField(max_length=10, choices=DocKind.choices)
    number = models.PositiveIntegerField("شماره‌ی نسخه")
    file = models.FileField(upload_to="cases/%Y/%m/")
    note = models.CharField("توضیح", max_length=250, blank=True)
    uploaded_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["kind", "number"]
        verbose_name = "نسخه‌ی سند"
        verbose_name_plural = "نسخه‌های سند"
        constraints = [models.UniqueConstraint(fields=["case", "kind", "number"],
                                               name="unique_version_number")]

    def __str__(self):
        return f"{self.get_kind_display()} v{self.number}"


class Review(models.Model):
    """رأی و نظر یک داور درباره‌ی یک نسخه‌ی پیشنهاد."""
    assignment = models.ForeignKey(RefereeAssignment, on_delete=models.CASCADE, related_name="reviews")
    version = models.ForeignKey(DocumentVersion, on_delete=models.PROTECT, related_name="reviews")
    decision = models.CharField(max_length=10, choices=ReviewDecision.choices)
    comment = models.TextField("نظر مکتوب", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "نظر داور"
        verbose_name_plural = "نظرات داوران"
        constraints = [models.UniqueConstraint(fields=["assignment", "version"],
                                               name="one_review_per_referee_per_version")]

    def clean(self):
        if self.decision == ReviewDecision.REVISE and not self.comment.strip():
            raise ValidationError("برای رأی «بازگشت جهت اصلاح» نظر مکتوب الزامی است.")
        if self.version.kind != DocKind.PROPOSAL or self.version.case_id != self.assignment.case_id:
            raise ValidationError("نسخه باید پیشنهادِ همین پرونده باشد.")


class Deadline(models.Model):
    case = models.ForeignKey(ProjectCase, on_delete=models.CASCADE, related_name="deadlines")
    kind = models.CharField(max_length=30, choices=DeadlineKind.choices)
    due_date = models.DateField("تاریخ مهلت")
    satisfied_at = models.DateField("تاریخ انجام", null=True, blank=True)

    class Meta:
        ordering = ["due_date"]
        verbose_name = "مهلت"
        verbose_name_plural = "مهلت‌ها"
        constraints = [models.UniqueConstraint(fields=["case", "kind"], name="one_deadline_per_kind")]

    @property
    def is_lower_bound(self):
        """DEFENSE_EARLIEST «زودترین تاریخ مجاز» است، نه سررسید؛ هرگز «عقب‌افتاده» حساب نمی‌شود."""
        return self.kind == DeadlineKind.DEFENSE_EARLIEST

    @property
    def is_overdue(self):
        return (not self.is_lower_bound and self.satisfied_at is None
                and timezone.localdate() > self.due_date)

    def __str__(self):
        return f"{self.get_kind_display()}: {self.due_date}"


class CaseEvent(models.Model):
    """دفتر رویدادهای فقط‌افزودنی (append-only)؛ سابقه‌ی حسابرسی و منبع Webhook فاز ۳."""
    case = models.ForeignKey(ProjectCase, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                              help_text="خالی = رویداد سیستمی")
    action = models.CharField(max_length=30)
    from_state = models.CharField(max_length=30, choices=State.choices)
    to_state = models.CharField(max_length=30, choices=State.choices)
    note = models.TextField(blank=True)
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "رویداد"
        verbose_name_plural = "رویدادها"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("رویدادها فقط‌افزودنی‌اند و قابل‌ویرایش نیستند.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("رویدادها قابل‌حذف نیستند.")

    def __str__(self):
        return f"{self.case_id}: {self.from_state} → {self.to_state}"
