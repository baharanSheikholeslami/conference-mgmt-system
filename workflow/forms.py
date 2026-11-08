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
from django.db.models import Case, IntegerField, When
from django.utils import timezone

from agents import services as agents

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

_CASE_WIDGETS = {
    "title_en": forms.TextInput(attrs={"dir": "ltr"}),
    "research_area": forms.TextInput(attrs={"placeholder": "مثلاً: هوش مصنوعی و پردازش زبان طبیعی"}),
    "interests": forms.Textarea(attrs={
        "rows": 4, "maxlength": services.MAX_INTERESTS_CHARS,
        "placeholder": "مثلاً: به مدل‌های زبانی بزرگ و خودکارسازی فرایندهای اداری دانشگاه علاقه دارم "
                       "و دوست دارم روی یک دستیار هوشمند کار کنم."}),
}
_CASE_HELP = {
    "research_area": "یک یا چند حوزه؛ موضوع پروژه را لازم نیست بدانید.",
    "interests": "سامانه با همین متن استادان راهنمای مرتبط را پیشنهاد می‌کند. موضوع دقیق پروژه پس از "
                 "بارگذاری پیشنهاد، از متن آن استخراج می‌شود.",
    "title_fa": "عنوان از متن پیشنهاد استخراج شده است؛ اگر درست نیست اصلاحش کنید. "
                "عنوان اصلاح‌شده دیگر با بارگذاری‌های بعدی عوض نمی‌شود.",
}


def _need_area_or_interests(form):
    data = form.cleaned_data
    if "research_area" in form.fields and not (data.get("research_area") or "").strip() \
            and not (data.get("interests") or "").strip():
        raise ValidationError("حوزه‌ی مورد علاقه یا توضیحی از علایق خود را بنویسید.")


class CaseCreateForm(forms.ModelForm):
    """دانشجو موضوعی انتخاب نمی‌کند؛ فقط حوزه یا توضیح علایقش را می‌نویسد."""

    class Meta:
        model = ProjectCase
        fields = ["research_area", "interests", "academic_year", "semester"]
        widgets = _CASE_WIDGETS
        help_texts = _CASE_HELP

    def clean(self):
        super().clean()
        _need_area_or_interests(self)
        return self.cleaned_data


class CaseEditForm(forms.ModelForm):
    """فیلدها بسته به نقش و وضعیت (services.editable_fields) کم و زیاد می‌شوند."""

    class Meta:
        model = ProjectCase
        fields = ["title_fa", "title_en", "research_area", "interests"]
        widgets = _CASE_WIDGETS
        help_texts = _CASE_HELP

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        allowed = services.editable_fields(self.instance, user)
        for name in list(self.fields):
            if name not in allowed:
                del self.fields[name]
        if "title_fa" in self.fields:
            self.fields["title_fa"].required = True

    def clean(self):
        super().clean()
        _need_area_or_interests(self)
        return self.cleaned_data


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

    def __init__(self, *args, case, transition, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.case = case
        self.transition = transition
        self.user = user          # برای نمایش پیشنهاد عامل‌ها (در حالت سایه فقط به آموزش)
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


def _ranked(queryset, ranks):
    """
    فاز ۳: اگر عامل تطبیق رتبه‌بندی داده باشد، فهرست استادان به همان ترتیب نمایش داده می‌شود.
    هیچ گزینه‌ای از پیش انتخاب نمی‌شود؛ تصمیم با انسان (دانشجو / مدیر گروه) است.
    """
    if ranks:
        order = Case(*[When(pk=pk, then=rank) for pk, rank in ranks.items()],
                     default=len(ranks) + 1, output_field=IntegerField())
        queryset = queryset.order_by(order, "last_name", "first_name")
    return queryset


def _rank_label(ranks, user):
    return f" (پیشنهاد سامانه: رتبه‌ی {fa_digits(ranks[user.pk])})" if user.pk in ranks else ""


class SelectAdvisorForm(ActionForm):
    advisor = forms.ModelChoiceField(label="استاد راهنما", queryset=None,
                                     widget=forms.RadioSelect, empty_label=None)

    def setup(self):
        field = self.fields["advisor"]
        ranks = agents.suggestion_ranks(self.case, "advisor", self.user)
        field.queryset = _ranked(services.eligible_advisors(), ranks)
        field.label_from_instance = lambda u: f"{u} — گروه {u.department}" + (
            f" — {u.expertise}" if u.expertise else "") + (
            f" — راهنمای {fa_digits(services.advisee_count(u))} از {fa_digits(rules.MAX_ADVISEES)} دانشجو"
        ) + _rank_label(ranks, u)
        field.help_text = ("درخواست شما برای استاد ارسال می‌شود و پس از پذیرش او پرونده به مرحله‌ی تدوین می‌رود. "
                           f"هر استاد حداکثر راهنمای {fa_digits(rules.MAX_ADVISEES)} دانشجوست؛ "
                           "استادانی که ظرفیتشان پر شده در فهرست نیستند.")
        del self.fields["note"]


class AssignRefereesForm(ActionForm):
    referees = forms.ModelMultipleChoiceField(label="داوران", queryset=None,
                                              widget=forms.CheckboxSelectMultiple)

    def setup(self):
        field = self.fields["referees"]
        ranks = agents.suggestion_ranks(self.case, "referee", self.user)
        field.queryset = _ranked(services.eligible_referees(self.case), ranks)
        field.label_from_instance = lambda u: (
            f"{u}" + (f" — {u.expertise}" if u.expertise else "") + _rank_label(ranks, u))
        field.help_text = (f"حداقل {fa_digits(self.case.required_referees)} داور از اعضای گروه "
                           f"{self.case.department} انتخاب کنید.")


class ReviewForm(ActionForm):
    decision = forms.ChoiceField(label="رأی", choices=ReviewDecision.choices,
                                 widget=forms.RadioSelect)

    def setup(self):
        self.fields["note"].label = "نظر مکتوب (برای «بازگشت جهت اصلاح» الزامی است)"


class RequestDefenseForm(ActionForm):
    """دانشجو چند روز و محل دفاع را پیشنهاد می‌دهد؛ استادان از میان ساعت‌های همین روزها انتخاب می‌کنند."""
    place = forms.CharField(label="محل برگزاری دفاع", max_length=200,
                            widget=forms.TextInput(attrs={"placeholder": "مثلاً کلاس ۲۰۱ دانشکده"}))

    def setup(self):
        today = timezone.localdate()
        earliest = rules.earliest_defense_day(self.case.final_approved_at, today)
        for i in range(1, rules.DEFENSE_MAX_DAYS + 1):
            required = i <= rules.DEFENSE_MIN_DAYS
            self.fields[f"day_{i}"] = JalaliDateField(
                label=f"روز پیشنهادی {fa_digits(i)}" + ("" if required else " (اختیاری)"), required=required)
        self.fields["day_1"].help_text = (
            f"زودترین تاریخ مجاز: {to_jalali(earliest)} (حداقل {fa_digits(rules.MIN_MONTHS_APPROVAL_TO_DEFENSE)} "
            "ماه پس از تصویب نهایی). جمعه‌ها پذیرفته نمی‌شود.")
        self.fields["place"].help_text = "این محل همراه با روز و ساعت دفاع به کارشناس آموزش اطلاع داده می‌شود."
        hours = "، ".join(services.hour_label(t) for t in rules.DEFENSE_TIME_SLOTS)
        self.fields["note"].help_text = (f"استاد راهنما و داور(ان) برای هر روز از میان {hours} زمان‌های آزاد "
                                         "خود را انتخاب می‌کنند و سامانه نخستین زمان مشترک را روز دفاع اعلام می‌کند.")
        self.order_fields([f"day_{i}" for i in range(1, rules.DEFENSE_MAX_DAYS + 1)] + ["place", "note"])

    def clean(self):
        data = super().clean()
        days = [data.get(f"day_{i}") for i in range(1, rules.DEFENSE_MAX_DAYS + 1)]
        days = [d for d in days if d]
        if len(set(days)) != len(days):
            raise ValidationError("هر روز را فقط یک بار وارد کنید.")
        return data

    def payload(self):
        days = [self.cleaned_data.get(f"day_{i}") for i in range(1, rules.DEFENSE_MAX_DAYS + 1)]
        return {"days": [d for d in days if d], "place": self.cleaned_data["place"]}


class PickDefenseTimeForm(ActionForm):
    """استاد راهنما یا داور زمان‌های آزاد خود را از میان روزهای پیشنهادی دانشجو انتخاب می‌کند."""
    slots = forms.MultipleChoiceField(label="روز و ساعت‌های آزاد شما", widget=forms.CheckboxSelectMultiple)

    def setup(self):
        request = services.open_defense_request(self.case)
        keys = request.slot_keys() if request else []
        groups = {}
        for key in keys:
            day = key.split("T")[0]
            groups.setdefault(day, []).append((key, services.hour_label(key.split("T")[1]).removeprefix("ساعت ")))
        self.fields["slots"].choices = [
            (services.day_label(date.fromisoformat(day)), items) for day, items in groups.items()]
        place = f" محل پیشنهادی دانشجو: {request.place}." if request else ""
        self.fields["slots"].help_text = (
            "ساعت‌ها، ساعت شروع جلسه‌اند. هر زمانی را که می‌توانید در جلسه‌ی دفاع باشید علامت بزنید (حداقل یکی). سامانه پس از انتخاب "
            "همه‌ی استادان، نخستین زمانی را که برای همه آزاد است روز دفاع اعلام می‌کند." + place)

    def payload(self):
        return {"slots": self.cleaned_data["slots"]}


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
    "request_defense": RequestDefenseForm,
    "advisor_pick_defense_time": PickDefenseTimeForm,
    "referee_pick_defense_time": PickDefenseTimeForm,
    "record_result": RecordResultForm,
}


def action_form_class(action):
    return ACTION_FORMS.get(action, ActionForm)
