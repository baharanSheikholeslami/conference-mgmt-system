"""
تحلیل حالت سایه (فاز ۴): خروجی عامل‌ها در برابر تصمیم واقعی انسان‌ها.

در حالت سایه عامل‌ها کار می‌کنند ولی کاربران خروجی‌شان را نمی‌بینند؛ پس تصمیم انسان «مستقل» از
پیشنهاد عامل گرفته شده است و می‌توان پرسید: اگر سامانه پیشنهادش را نشان داده بود، چقدر با تصمیم
انسان هم‌خوان بود؟ این تابع‌ها فقط از پایگاه‌داده می‌خوانند و هیچ چیزی را تغییر نمی‌دهند.
"""
from statistics import median

from agents.models import AssistantMessage, DocumentValidation, Notification, SuggestionRun
from workflow.models import CaseEvent, ProjectCase

from .runner import PREFIX


def _cases(include_scenarios):
    cases = ProjectCase.objects.all()
    return cases if include_scenarios else cases.exclude(author__username__startswith=PREFIX)


def _rank_stats(ranks, k=3):
    """ranks: رتبه‌ی گزینه‌ی انسان در فهرست عامل (None = در فهرست نبود)."""
    n = len(ranks)
    if not n:
        return {"n": 0}
    return {"n": n,
            "hit1": sum(r == 1 for r in ranks) / n,
            f"hit{k}": sum(r is not None and r <= k for r in ranks) / n,
            "mrr": sum(1 / r for r in ranks if r) / n}


def matching(purpose, include_scenarios=False):
    """
    پیشنهاد داور / استاد راهنما در برابر انتخاب واقعی: برای هر انتخاب انسان، رتبه‌ی آن در فهرست عامل.
    فقط پیشنهادهایی حساب می‌شوند که «پیش از» تصمیم انسان آماده بوده‌اند.
    """
    action, key = ("assign_referees", "referee_ids") if purpose == "referee" else ("select_advisor", "advisor_id")
    ranks = []
    runs = (SuggestionRun.objects.filter(purpose=purpose, status=SuggestionRun.Status.DONE,
                                         case__in=_cases(include_scenarios))
            .prefetch_related("suggestions"))
    for run in runs:
        event = (CaseEvent.objects.filter(case=run.case, action=action, created_at__gte=run.finished_at)
                 .order_by("created_at").first())
        if event is None:
            continue
        chosen = event.data.get(key)
        chosen = chosen if isinstance(chosen, list) else [chosen]
        rank_of = {s.referee_id: s.rank for s in run.suggestions.all()}
        ranks += [rank_of.get(pk) for pk in chosen if pk is not None]
    return _rank_stats(ranks)


def validation_vs_advisor(include_scenarios=False):
    """
    بررسی قالب در برابر تصمیم استاد راهنما درباره‌ی همان نسخه (تأیید یا بازگرداندن).
    این فقط یک «نشانه» است: استاد ممکن است به دلیلی جز قالب پیشنهاد را برگرداند.
    """
    table = {}
    validations = (DocumentValidation.objects.exclude(status__in=["pending", "error"])
                   .filter(version__case__in=_cases(include_scenarios)).select_related("version"))
    for v in validations:
        decision = (CaseEvent.objects.filter(case=v.version.case, action__in=["advisor_approve", "advisor_return"],
                                             created_at__gte=v.version.uploaded_at)
                    .order_by("created_at").first())
        if decision is None:
            continue
        cell = (v.status, decision.action)
        table[cell] = table.get(cell, 0) + 1
    return table


def notifications(include_scenarios=False):
    rows = Notification.objects.filter(case__in=_cases(include_scenarios))
    return {"total": rows.count(),
            "sent": rows.filter(status=Notification.Status.SENT).count(),
            "failed": rows.filter(status=Notification.Status.FAILED).count(),
            "queued": rows.filter(status=Notification.Status.QUEUED).count(),
            "llm": rows.filter(generated_by="llm").count(),
            "template": rows.filter(generated_by="template").count(),
            "reminders": rows.filter(kind=Notification.Kind.REMINDER).count()}


def assistant():
    rows = list(AssistantMessage.objects.all())
    ok = [r for r in rows if r.ok]
    cited = [r for r in ok if any(s.get("cited") for s in r.sources)]
    latencies = [r.latency_ms for r in ok if r.latency_ms]
    return {"total": len(rows), "ok": len(ok), "cited": len(cited),
            "median_ms": median(latencies) if latencies else None}


def _pct(x):
    return "—" if x is None else f"{x * 100:.0f}٪"


def markdown(include_scenarios=False):
    referee, advisor = matching("referee", include_scenarios), matching("advisor", include_scenarios)
    lines = ["# گزارش حالت سایه", "",
             "خروجی عامل‌ها در برابر تصمیم واقعی انسان‌ها (فقط خواندنی؛ پرونده‌های سناریو "
             + ("هم حساب شده‌اند" if include_scenarios else "حساب نشده‌اند") + ").", "",
             "## ۱. تطبیق استاد", "",
             "| عامل | تعداد تصمیم | گزینه‌ی انسان = رتبه‌ی ۱ | در ۳ رتبه‌ی اول | MRR |", "|---|---|---|---|---|"]
    for label, s in (("پیشنهاد داور", referee), ("پیشنهاد استاد راهنما", advisor)):
        mrr = f"{s['mrr']:.2f}" if s["n"] else "—"
        lines.append(f"| {label} | {s['n']} | {_pct(s.get('hit1'))} | {_pct(s.get('hit3'))} | {mrr} |")
    table = validation_vs_advisor(include_scenarios)
    lines += ["", "## ۲. بررسی قالب در برابر تصمیم استاد راهنما", "",
              "| نتیجه‌ی عامل | استاد تأیید کرد | استاد برگرداند |", "|---|---|---|"]
    for status, label in (("passed", "مطابق قالب"), ("issues", "نیازمند توجه")):
        lines.append(f"| {label} | {table.get((status, 'advisor_approve'), 0)} "
                     f"| {table.get((status, 'advisor_return'), 0)} |")
    n = notifications(include_scenarios)
    lines += ["", "## ۳. اعلان‌ها", "",
              f"- کل: {n['total']} (یادآوری مهلت: {n['reminders']})",
              f"- ارسال‌شده: {n['sent']} — ناموفق: {n['failed']} — هنوز در صف: {n['queued']}",
              f"- متن مدل زبانی: {n['llm']} — متن ثابت پشتیبان: {n['template']}"]
    a = assistant()
    latency = "—" if a["median_ms"] is None else f"{a['median_ms'] / 1000:.1f} ثانیه"
    lines += ["", "## ۴. دستیار آیین‌نامه", "",
              f"- پرسش‌ها: {a['total']} — پاسخ موفق: {a['ok']} — پاسخ با استناد به بند: {a['cited']}",
              f"- میانه‌ی زمان پاسخ: {latency}"]
    return "\n".join(lines) + "\n"
