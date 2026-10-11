"""گزارش اجرای سناریوها به Markdown (برای خواندن) و JSON (ورودی اسکریپت‌های ارزیابی فاز ۵)."""
import json

from django.utils import timezone

from workflow.jalali import fa_digits, to_jalali


def _mark(value):
    return {True: "✓", False: "✗", None: "—"}[value]


def markdown(results, live, start):
    total = sum(len(r.steps) for r in results)
    passed = sum(r.passed for r in results)
    lines = [
        "# گزارش اجرای سناریوهای سرتاسری (فاز ۴)",
        "",
        f"- زمان اجرا: {to_jalali(timezone.now())} — {timezone.localtime().strftime('%H:%M')}",
        f"- حالت: {'زنده (پورتال + n8n + مدل زبانی)' if live else 'آفلاین (فقط موتور گردش‌کار و قواعد مهلت)'}",
        f"- روز شروع شبیه‌سازی: {to_jalali(start)}",
        f"- سناریوهای موفق: {fa_digits(sum(r.ok for r in results))} از {fa_digits(len(results))}"
        f" — گام‌های موفق: {fa_digits(passed)} از {fa_digits(total)}",
        "",
        "| سناریو | گام‌ها | نتیجه |",
        "|---|---|---|",
    ]
    for r in results:
        lines.append(f"| {r.scenario.title} | {fa_digits(r.passed)}/{fa_digits(len(r.scenario.steps))} "
                     f"| {'✓' if r.ok and len(r.steps) == len(r.scenario.steps) else '✗'} |")

    observations = [(r, o) for r in results for o in r.observations]
    if observations:
        lines += ["", "## خروجی عامل‌ها در برابر پاسخ درست", "",
                  "| سناریو | عامل | پاسخ درست | خروجی عامل | تطابق | زمان (ثانیه) | جزئیات |",
                  "|---|---|---|---|---|---|---|"]
        for r, o in observations:
            lines.append(f"| {r.scenario.key} | {o.agent} | {o.expected} | {o.got} | {_mark(o.match)} "
                         f"| {o.seconds:.0f} | {o.detail} |")

    for r in results:
        lines += ["", f"## {r.scenario.title}", "", r.scenario.description, "",
                  "| # | روز | بازیگر | اقدام | انتظار | نتیجه | ✓ | توضیح |", "|---|---|---|---|---|---|---|---|"]
        for i, s in enumerate(r.steps, 1):
            detail = " — ".join(x for x in (s.note, s.detail) if x)
            lines.append(f"| {fa_digits(i)} | {to_jalali(s.day)} | {s.actor} | `{s.action}` | {s.expected} "
                         f"| {s.got} | {_mark(s.ok)} | {detail} |")
        skipped = len(r.scenario.steps) - len(r.steps)
        if r.error:
            lines.append(f"\n**خطای برنامه:** {r.error}")
        if skipped:
            lines.append(f"\n{fa_digits(skipped)} گام بعدی اجرا نشد.")
    return "\n".join(lines) + "\n"


def as_json(results, live, start):
    return json.dumps({
        "live": live,
        "start": start.isoformat(),
        "scenarios": [{
            "key": r.scenario.key, "title": r.scenario.title, "ok": r.ok, "error": r.error,
            "steps": [{"day": s.day.isoformat(), "actor": s.actor, "action": s.action,
                       "expected": s.expected, "got": s.got, "ok": s.ok, "detail": s.detail}
                      for s in r.steps],
            "observations": [o.__dict__ for o in r.observations],
        } for r in results],
    }, ensure_ascii=False, indent=2)
