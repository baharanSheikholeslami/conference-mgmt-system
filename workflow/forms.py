"""
فرم‌های پورتال.

فرم‌ها عمداً «لاغر» هستند: فقط ورودی کاربر را می‌گیرند و به نوع درست تبدیل می‌کنند.
قواعد گردش‌کار (چه کسی، در چه وضعیتی، با چه پیش‌شرطی) فقط در services.py بررسی می‌شود
تا همان قواعد برای فرم وب، پنل ادمین و Webhookهای فاز ۳ یکسان باشد.
"""
from datetime import date

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator

from . import rules, services
from .enums import ReviewDecision
from .jalali import fa_digits, parse_jalali, to_jalali
from .models import ProjectCase

MAX_UPLOAD_MB = 10
ALLOWED_EXTENSIONS = ["pdf", "doc", "docx"]


class JalaliDateField(forms.CharField):
    """کاربر تاریخ شمسی تایپ می‌کند (۱۴۰۵/۱۰/۱۵)؛ مقدار پاک‌شده یک date میلادی است."""

    def __init__(self, **kwargs):
        kwargs.setdefault("widget", forms.TextInput(attrs={
            "placeholder": "مثلاً ۱۴۰۵/۱۰/۱۵", "dir": "ltr", "inputmode": "numeric"}))
        super().__init__(**kwargs)

    def to_python(self, value):
        value = super().to_python(value)
        if not value:
            return None
        try:
            return parse_jalali(value)
        except ValueError:
            raise ValidationError("تاریخ را به‌صورت شمسی و به شکل ۱۴۰۵/۱۰/۱۵ وارد کنید.") from None

    def prepare_value(self, value):
        return to_jalali(value, fa=False) if isinstance(value, date) else value


# ───────────────────────── پرونده و سند ─────────────────────────

class CaseCreateForm(forms.ModelForm):
    class Meta:
        model = ProjectCase
        fields = ["title_fa", "title_en", "academic_year", "semester"]
        widgets = {"title_en": forms.TextInput(attrs={"dir": "ltr"})}


class CaseEditForm(forms.ModelForm):
    class Meta:
        model = ProjectCase
        fields = ["title_fa", "title_en"]
        widgets = {"title_en": forms.TextInput(attrs={"dir": "ltr"})}


def _max_size(file):
    if file.size > MAX_UPLOAD_MB * 1024 * 1024:
        raise ValidationError(f"حجم فایل نباید بیشتر از {fa_digits(MAX_UPLOAD_MB)} مگابایت باشد.")


class UploadForm(forms.Form):
    file = forms.FileField(
        label="فایل", validators=[FileExtensionValidator(ALLOWED_EXTENSIONS), _max_size],
        help_text=f"قالب‌های مجاز: PDF و Word — حداکثر {fa_digits(MAX_UPLOAD_MB)} مگابایت",
        widget=forms.ClearableFileInput(attrs={"accept": ".pdf,.doc,.docx"}))
    note = forms.CharField(label="توضیح این نسخه (اختیاری)", max_length=250, required=False)


# ───────────────────────── فرم‌های اقدام (گذارهای ماشین‌حالت) ─────────────────────────

class ActionForm(forms.Form):
    """
    پایه‌ی همه‌ی اقدام‌ها: فقط یک فیلد «توضیح» که الزامی‌بودنش را خودِ ماشین‌حالت تعیین می‌کند
    (transition.note_required). اقدام‌هایی که ورودی بیشتری دارند از این کلاس ارث می‌برند.
    """
    note = forms.CharField(label="توضیح", required=False,
                           widget=forms.Textarea(attrs={"rows": 4}))

    def __init__(self, *args, case, transition, **kwargs):
        super().__init__(*args, **kwargs)
        self.case = case
        self.transition = transition
        note = self.fields["note"]
        note.required = transition.note_required
        note.label = "توضیح (الزامی)" if transition.note_required else "توضیح (اختیاری)"
        self.setup()
        self.order_fields([name for name in self.fields if name != "note"] + ["note"])  # توضیح همیشه آخر

    def setup(self):
        """زیرکلاس‌ها فیلدهای وابسته به پرونده را این‌جا تنظیم می‌کنند."""

    def payload(self):
        """ورودی‌های اضافه‌ی perform() — همه‌ی فیلدها به‌جز توضیح."""
        return {k: v for k, v in self.cleaned_data.items() if k != "note"}


class SelectAdvisorForm(ActionForm):
    advisor = forms.ModelChoiceField(label="استاد راهنما", queryset=None,
                                     widget=forms.RadioSelect, empty_label=None)

    def setup(self):
        field = self.fields["advisor"]
        field.queryset = services.eligible_advisors()
        field.label_from_instance = lambda u: f"{u} — گروه {u.department}" + (
            f" — {u.expertise}" if u.expertise else "")
        del self.fields["note"]


class AssignRefereesForm(ActionForm):
    referees = forms.ModelMultipleChoiceField(label="داوران", queryset=None,
                                              widget=forms.CheckboxSelectMultiple)

    def setup(self):
        field = self.fields["referees"]
        field.queryset = services.eligible_referees(self.case)
        field.label_from_instance = lambda u: f"{u}" + (f" — {u.expertise}" if u.expertise else "")
        field.help_text = (f"حداقل {fa_digits(self.case.required_referees)} داور از اعضای گروه "
                           f"{self.case.department} انتخاب کنید.")


class ReviewForm(ActionForm):
    decision = forms.ChoiceField(label="رأی", choices=ReviewDecision.choices,
                                 widget=forms.RadioSelect)

    def setup(self):
        self.fields["note"].label = "نظر مکتوب (برای «بازگشت جهت اصلاح» الزامی است)"


class ScheduleDefenseForm(ActionForm):
    defense_date = JalaliDateField(label="تاریخ دفاع (شمسی)")
    defense_place = forms.CharField(label="محل دفاع", max_length=200)

    def setup(self):
        earliest = rules.add_months(self.case.final_approved_at,
                                    rules.MIN_MONTHS_APPROVAL_TO_DEFENSE)
        self.fields["defense_date"].help_text = f"زودترین تاریخ مجاز: {to_jalali(earliest)}"


class RecordResultForm(ActionForm):
    grade = forms.DecimalField(label="نمره (از ۲۰)", min_value=0, max_value=20, decimal_places=2,
                               widget=forms.NumberInput(attrs={"step": "0.25", "dir": "ltr"}))

    def setup(self):
        if not self.case.is_graded:          # نویسنده غیردانشجو مشمول نمره‌دهی نیست
            del self.fields["grade"]


ACTION_FORMS = {
    "select_advisor": SelectAdvisorForm,
    "assign_referees": AssignRefereesForm,
    "submit_review": ReviewForm,
    "schedule_defense": ScheduleDefenseForm,
    "record_result": RecordResultForm,
}


def action_form_class(action):
    return ACTION_FORMS.get(action, ActionForm)
