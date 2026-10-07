"""
منطق لایه‌ی عامل‌ها در سمت پورتال — پل میان گردش‌کار (workflow) و n8n.

تقسیم کار (بخش ۳-ج پروپوزال):
  • پورتال: داده، قواعد قطعی (چه کسی، چه مهلتی، چه گیرنده‌ای) و نمایش نتیجه به انسان
  • n8n   : هماهنگی عامل‌ها — فراخوانی مدل زبانی و embedding، رتبه‌بندی، نوشتن و ارسال ایمیل
هر عامل دو تابع دارد: request_*/ask/notify (پورتال ← n8n) و save_*/record_* (n8n ← پورتال).

دو اصل که در همه‌ی توابع رعایت شده است:
  ۱. انسان‌در‌حلقه — این فایل هیچ‌وقت perform() را صدا نمی‌زند و وضعیت پرونده را عوض نمی‌کند.
  ۲. پورتال بدون عامل‌ها هم کار می‌کند — خرابی n8n فقط به «خطای عامل» تبدیل می‌شود، نه خطای پورتال.
"""
import logging
import re
import time
from numbers import Real

from django.conf import settings
from django.db import transaction
from django.db.models import Count
from django.urls import reverse
from django.utils import timezone
from pgvector.django import CosineDistance

from workflow import rules
from workflow import services as wf
from workflow import statemachine as sm
from workflow.enums import CaseRole, DeadlineKind, DocKind, State, UserType
from workflow.jalali import fa_digits, to_jalali
from workflow.models import (CaseEvent, Deadline, DocumentVersion, RefereeAssignment, User)

from . import checklist, chunking, extract, n8n
from .models import (AssistantMessage, DocumentValidation, Notification, PolicyChunk,
                     RefereeSuggestion, SuggestionRun)

log = logging.getLogger("agents")


class AgentError(Exception):
    """کار عامل انجام نشد؛ متن خطا فارسی و قابل‌نمایش به کاربر است."""


def enabled():
    return settings.AGENTS_ENABLED


def _finish(run, status=None, error="", model=""):
    """یک درخواست ناهم‌زمان را می‌بندد؛ بدون status یعنی «انجام نشد» (error دلیل آن است)."""
    run.status = status or run.Status.ERROR
    run.error = error[:300]
    run.model = (model or "")[:120]
    run.finished_at = timezone.now()
    run.save()
    return run


def _restart(model, extra=None, **lookup):
    """درخواست تازه: ردیف را (پیش از تماس با n8n) به «در انتظار» برمی‌گرداند."""
    run, _ = model.objects.update_or_create(**lookup, defaults={
        "status": model.Status.PENDING, "error": "", "model": "",
        "requested_at": timezone.now(), "finished_at": None, **(extra or {})})
    return run


# ───────────────────────── ۱. عامل اعتبارسنجی سند ─────────────────────────

MIN_DOC_CHARS = 300          # کمتر از این یعنی عملاً متنی استخراج نشده (مثلاً PDF اسکن‌شده)
MAX_DOC_CHARS = 60_000       # سقف متنی که برای n8n فرستاده می‌شود


def request_validation(version):
    """بررسی قالب این نسخه‌ی پیشنهاد را از n8n می‌خواهد. نتیجه بعداً با save_validation_result می‌رسد."""
    validation = _restart(DocumentValidation, extra={"items": []}, version=version)
    try:
        text = extract.extract_text(version.file)
    except extract.ExtractionError as e:
        return _finish(validation, error=str(e))
    if len(text) < MIN_DOC_CHARS:
        return _finish(validation, error="متن قابل‌خواندنی در این فایل پیدا نشد (شاید PDF اسکن‌شده "
                                         "باشد)؛ بررسی خودکار قالب ممکن نیست.")
    case = version.case
    payload = {
        "validation_id": validation.pk,
        "case": {"id": case.pk, "title_fa": case.title_fa, "title_en": case.title_en},
        "document": {"version": version.number, "chars": len(text),
                     "truncated": len(text) > MAX_DOC_CHARS, "text": text[:MAX_DOC_CHARS]},
        "checklist": checklist.ITEMS,
    }
    try:
        n8n.post("validate-proposal", payload)
    except n8n.Unavailable as e:
        log.warning("validate-proposal: %s", e)
        return _finish(validation, error="سرویس بررسی خودکار در دسترس نیست؛ کمی بعد دوباره تلاش کنید.")
    return validation      # پس از تماس با n8n چیزی ذخیره نمی‌شود تا پاسخ زودهنگام n8n بازنویسی نشود


def save_validation_result(validation, data):
    """
    پاسخ n8n: {"found": [{"key", "evidence"}], "unverified": [{"key", "evidence"}],
               "windows": n, "failed_windows": k, "model": "..."}  یا  {"error": "...", "detail": "..."}
    فقط کلیدهای چک‌لیست خودمان پذیرفته می‌شود؛ هر چیز دیگری که مدل ساخته باشد نادیده گرفته می‌شود.
    """
    model = str(data.get("model") or "")
    if data.get("error"):
        log.warning("validation %s: %s — %s", validation.pk, data["error"], data.get("detail", ""))
        return _finish(validation, error=str(data["error"]), model=model)

    def by_key(name):
        rows = data.get(name) or []
        return {r.get("key"): r for r in rows if isinstance(r, dict)}

    found, unverified = by_key("found"), by_key("unverified")
    items = []
    for item in checklist.ITEMS:
        hit = found.get(item["key"])
        if hit:
            note = f"شاهد در متن: «{str(hit.get('evidence') or '').strip()[:150]}»"
        elif item["key"] in unverified:
            note = ("مدل زبانی این مورد را «موجود» اعلام کرد، اما شاهد آن در متن سند تأیید نشد؛ "
                    "لطفاً خودتان بررسی کنید.")
        elif item["group"] == checklist.FIELD:
            note = "خالی است یا در متن سند پیدا نشد."
        else:
            note = "این بخش در متن سند پیدا نشد."
        items.append({"key": item["key"], "label": item["label"], "group": item["group"],
                      "ok": bool(hit), "note": note})
    validation.items = items
    failed, total = data.get("failed_windows") or 0, data.get("windows") or 0
    warning = (f"بررسی {fa_digits(failed)} تکه از {fa_digits(total)} تکه‌ی سند ناموفق بود؛ "
               "نتیجه ممکن است ناقص باشد.") if failed else ""
    status = (DocumentValidation.Status.PASSED if all(i["ok"] for i in items)
              else DocumentValidation.Status.ISSUES)
    return _finish(validation, status=status, error=warning, model=model)


def can_retry_validation(version, user):
    return user.is_superuser or user.id == version.case.author_id or user.user_type == UserType.EDU_STAFF


# ───────────────────────── ۲. دستیار دانش (RAG) ─────────────────────────

EMBED_BATCH = 16
MAX_QUESTION_CHARS = 500
ASK_TIMEOUT = 300            # مدل محلی ممکن است کند باشد


def embed(texts):
    """متن‌ها ← بردارها، از راه گردش‌کار rag-embed در n8n. خروجی: (فهرست بردارها، نام مدل)."""
    vectors, model = [], ""
    for start in range(0, len(texts), EMBED_BATCH):
        batch = texts[start:start + EMBED_BATCH]
        data = n8n.post("rag-embed", {"texts": batch}, timeout=ASK_TIMEOUT)
        got = data.get("vectors")
        if not isinstance(got, list) or len(got) != len(batch):
            raise n8n.Unavailable("پاسخ embedding از n8n ناقص بود")
        vectors += got
        model = str(data.get("model") or model)
    return vectors, model


def index_document(path, doc_code, doc_title=""):
    """سند را قطعه‌بندی، بردارسازی و جایگزین نمایه‌ی قبلی همین doc_code می‌کند. خروجی: (تعداد قطعه، مدل)."""
    try:
        text = extract.extract_text(path)
    except extract.ExtractionError as e:
        raise AgentError(str(e)) from e
    chunks = chunking.split(text)
    if not chunks:
        raise AgentError("متنی برای نمایه‌سازی در این فایل پیدا نشد.")
    try:
        vectors, model = embed([f"{c.ref}\n{c.text}" if c.ref else c.text for c in chunks])
    except n8n.Unavailable as e:
        raise AgentError(str(e)) from e
    with transaction.atomic():
        PolicyChunk.objects.filter(doc_code=doc_code).delete()
        PolicyChunk.objects.bulk_create([
            PolicyChunk(doc_code=doc_code, doc_title=doc_title, position=i, ref=c.ref[:200],
                        text=c.text, embedding=vector, embedding_model=model)
            for i, (c, vector) in enumerate(zip(chunks, vectors), 1)])
    return len(chunks), model


def search_policy(embedding, model, k=5):
    """نزدیک‌ترین قطعه‌ها به بردار پرسش (شباهت کسینوسی در pgvector). ابزار بازیابیِ گردش‌کار rag-ask."""
    if (not isinstance(embedding, list) or not embedding
            or not all(isinstance(x, Real) and not isinstance(x, bool) for x in embedding)):
        raise AgentError("بردار پرسش نامعتبر است.")
    chunks = PolicyChunk.objects.filter(embedding_model=model)
    if not chunks.exists():
        raise AgentError(f"پایگاه دانش با مدل embedding «{model}» نمایه نشده است؛ "
                         "دستور index_policy را اجرا کنید.")
    k = max(1, min(k, 10)) if isinstance(k, int) and not isinstance(k, bool) else 5
    nearest = chunks.annotate(distance=CosineDistance("embedding", embedding)).order_by("distance")[:k]
    return [{"id": c.pk, "doc": c.doc_code, "title": c.doc_title, "ref": c.ref, "text": c.text,
             "score": round(1 - float(c.distance), 4)} for c in nearest]


def ask_assistant(user, question):
    """پرسش کاربر را به گردش‌کار rag-ask می‌دهد و پاسخ مستند را برمی‌گرداند (و در سابقه ثبت می‌کند)."""
    question = " ".join((question or "").split())
    if not question:
        raise AgentError("پرسش خود را بنویسید.")
    if len(question) > MAX_QUESTION_CHARS:
        raise AgentError(f"پرسش باید کوتاه‌تر از {fa_digits(MAX_QUESTION_CHARS)} نویسه باشد.")
    if not PolicyChunk.objects.exists():
        raise AgentError("پایگاه دانش دستیار هنوز نمایه نشده است.")

    started = time.monotonic()

    def record(**fields):
        return AssistantMessage.objects.create(
            user=user, question=question, latency_ms=round((time.monotonic() - started) * 1000),
            **fields)

    try:
        data = n8n.post("rag-ask", {"question": question}, timeout=ASK_TIMEOUT)
    except n8n.Unavailable as e:
        log.warning("rag-ask: %s", e)
        record(ok=False, error=str(e)[:300])
        raise AgentError("دستیار در حال حاضر در دسترس نیست؛ کمی بعد دوباره تلاش کنید.") from e
    answer = str(data.get("answer") or "").strip()
    if not answer:
        record(ok=False, error="پاسخ خالی از n8n")
        raise AgentError("دستیار پاسخی تولید نکرد؛ دوباره تلاش کنید.")
    sources = _sources(data.get("sources"))
    record(answer=answer, sources=sources, model=str(data.get("model") or "")[:120])
    return {"answer": answer, "sources": sources}


def _sources(raw):
    """منابعی که n8n برگردانده را با متن واقعی قطعه‌ها در پایگاه‌داده کامل می‌کند."""
    rows = [r for r in (raw or []) if isinstance(r, dict) and isinstance(r.get("chunk_id"), int)]
    chunks = PolicyChunk.objects.in_bulk([r["chunk_id"] for r in rows])
    return [{"n": r.get("n"), "doc": chunks[r["chunk_id"]].doc_code, "ref": chunks[r["chunk_id"]].ref,
             "text": chunks[r["chunk_id"]].text, "score": r.get("score"), "cited": bool(r.get("cited"))}
            for r in rows if r["chunk_id"] in chunks]


# ───────────────────────── ۳. عامل پیشنهاد داور ─────────────────────────

TOPIC_EXCERPT_CHARS = 2000
EXPLAIN_TOP = 5
# سطر عنوان «مقدمه»؛ شماره‌ی بخش در متن استخراج‌شده از PDF گاهی به انتهای سطر می‌افتد
_INTRO_HEADING = re.compile(r"^[\d۰-۹.\-–) ]*مقدمه[\d۰-۹.\-–) ]*$", re.M)


def topic_text(case):
    """متنی که «موضوع پیشنهاد» را نشان می‌دهد: عنوان‌ها + ابتدای مقدمه‌ی آخرین نسخه‌ی پیشنهاد."""
    parts = [case.title_fa, case.title_en]
    version = wf.latest_version(case, DocKind.PROPOSAL)
    if version:
        try:
            text = extract.extract_text(version.file)
        except extract.ExtractionError:
            text = ""
        intro = _INTRO_HEADING.search(text)       # صفحه‌ی اول فرم برای همه‌ی پیشنهادها یکسان است
        start = intro.end() if intro else 0
        parts.append(text[start:start + TOPIC_EXCERPT_CHARS])
    return "\n".join(p.strip() for p in parts if p and p.strip())


def request_referee_suggestions(case):
    """
    رتبه‌بندی معنایی داوران واجد شرایط را از n8n می‌خواهد. «واجد شرایط بودن» قاعده‌ی قطعی
    است و همین‌جا (eligible_referees) تعیین می‌شود؛ n8n فقط همین فهرست را مرتب می‌کند.
    """
    run = _restart(SuggestionRun, case=case)
    candidates = list(wf.eligible_referees(case))
    if not candidates:
        return _finish(run, error="در این گروه آموزشی داور واجد شرایطی ثبت نشده است.")
    payload = {
        "run_id": run.pk,
        "case": {"id": case.pk, "title_fa": case.title_fa, "title_en": case.title_en,
                 "department": str(case.department)},
        "query_text": topic_text(case),
        "candidates": [{"id": u.pk, "name": str(u), "expertise": u.expertise.strip()}
                       for u in candidates],
        "explain_top": EXPLAIN_TOP,
    }
    try:
        n8n.post("suggest-referees", payload)
    except n8n.Unavailable as e:
        log.warning("suggest-referees: %s", e)
        return _finish(run, error="سرویس پیشنهاد داور در دسترس نیست؛ کمی بعد دوباره تلاش کنید.")
    return run


def save_referee_suggestions(run, data):
    """
    پاسخ n8n: {"suggestions": [{"referee_id", "score", "reason"}], "model": "..."} یا {"error": "..."}
    فقط داورانی ذخیره می‌شوند که واقعاً واجد شرایط همین پرونده‌اند؛ رتبه را خود پورتال از روی score می‌سازد.
    """
    model = str(data.get("model") or "")
    if data.get("error"):
        log.warning("referee suggestions %s: %s — %s", run.pk, data["error"], data.get("detail", ""))
        return _finish(run, error=str(data["error"]), model=model)
    eligible = set(wf.eligible_referees(run.case).values_list("pk", flat=True))
    rows = {}
    for s in data.get("suggestions") or []:
        pk = s.get("referee_id") if isinstance(s, dict) else None
        if not isinstance(pk, int) or pk not in eligible or pk in rows:
            continue
        score = s.get("score")
        score = float(score) if isinstance(score, Real) and not isinstance(score, bool) else None
        rows[pk] = (score, str(s.get("reason") or "").strip()[:600])
    ordered = sorted(rows.items(), key=lambda row: (row[1][0] is None, -(row[1][0] or 0)))
    with transaction.atomic():
        run.suggestions.all().delete()
        RefereeSuggestion.objects.bulk_create([
            RefereeSuggestion(run=run, referee_id=pk, rank=rank, score=score, reason=reason)
            for rank, (pk, (score, reason)) in enumerate(ordered, 1)])
        _finish(run, status=SuggestionRun.Status.DONE, model=model)
    return run


def suggestion_ranks(case):
    """{شناسه‌ی داور: رتبه} برای مرتب‌کردن فرم تخصیص داور؛ خالی اگر پیشنهادی آماده نباشد."""
    if not enabled():
        return {}
    return dict(RefereeSuggestion.objects.filter(run__case=case, run__status=SuggestionRun.Status.DONE)
                .values_list("referee_id", "rank"))


def suggestion_panel(case):
    """داده‌ی کادر «پیشنهاد سامانه» در فرم تخصیص داور (یا None اگر عامل‌ها خاموش باشند)."""
    if not enabled():
        return None
    run = SuggestionRun.objects.filter(case=case).first()
    rows = list(run.suggestions.select_related("referee")) if run else []
    load = dict(RefereeAssignment.objects.filter(referee__in=[r.referee_id for r in rows])
                .exclude(case__state__in=sm.TERMINAL_STATES)
                .values_list("referee").annotate(n=Count("id")))
    for row in rows:
        row.load = load.get(row.referee_id, 0)
    return {"run": run, "rows": rows}


def can_refresh_suggestions(case, user):
    return case.state == State.REFEREE_ASSIGNMENT and CaseRole.HEAD in case.roles_of(user)


# ───────────────────────── ۴. محرک اطلاع‌رسانی ─────────────────────────

FOOTER = ("برای مشاهده‌ی جزئیات و انجام اقدام وارد پورتال شوید:\n{link}\n\n"
          "این پیام خودکار و صرفاً برای اطلاع است؛ لطفاً به آن پاسخ ندهید.")


def _unique(users):
    seen, result = set(), []
    for u in users:
        if u is not None and u.pk not in seen:
            seen.add(u.pk)
            result.append(u)
    return result


def stakeholders(case):
    """همه‌ی کسانی که در این پرونده نقش دارند (نویسنده، راهنما، مدیر گروه، داوران، آموزش)."""
    users = [case.author, case.advisor]
    if case.department_id:
        users += User.objects.filter(is_department_head=True, department_id=case.department_id)
    users += [a.referee for a in case.referee_assignments.select_related("referee")]
    users += User.objects.filter(user_type=UserType.EDU_STAFF)
    return [u for u in _unique(users) if u.is_active]


def pending_actions(case, user):
    """عنوان اقدام‌هایی که همین حالا نوبت این کاربر است (همان منطق «در انتظار اقدام» داشبورد)."""
    return [o.transition.label for o in wf.available_actions(case, user) if o.pending]


def days_phrase(days_left):
    if days_left > 0:
        return f"{fa_digits(days_left)} روز مانده"
    return "امروز" if days_left == 0 else f"{fa_digits(-days_left)} روز گذشته"


def _notification(key, user, case, subject, facts, sentences):
    """قالب مشترک یک اعلان برای n8n. facts ورودی مدل زبانی و fallback_body متن پشتیبان قطعی است."""
    actions = pending_actions(case, user)
    lines = [f"{user} گرامی،", *sentences, f"وضعیت کنونی پرونده: «{case.get_state_display()}»."]
    if actions:
        lines.append("اقدام مورد انتظار از شما: " + "، ".join(actions) + ".")
    link = settings.PORTAL_PUBLIC_URL + reverse("case_detail", args=[case.pk])
    return {
        "key": key, "to": user.email, "subject": subject[:250],
        "facts": {"recipient_name": str(user),
                  "recipient_roles": [r.label for r in sorted(case.roles_of(user))],
                  "case_title": case.title_fa, "case_state": case.get_state_display(),
                  "your_actions": actions, **facts},
        "fallback_body": "\n".join(lines),
        "footer": FOOTER.format(link=link),
    }


def _deadline_facts(deadline, today):
    days_left = (deadline.due_date - today).days
    return {"label": deadline.get_kind_display(), "date": to_jalali(deadline.due_date),
            "days_left": days_left, "status": days_phrase(days_left)}


def build_event_notifications(event):
    """
    گیرندگان یک تغییر وضعیت: هر که اکنون نوبت اقدام اوست + نویسنده (+ استاد راهنما در پایان پرونده)،
    به‌جز خود انجام‌دهنده‌ی اقدام و کسانی که ایمیل ندارند.
    """
    case, today = event.case, timezone.localdate()
    people = [u for u in stakeholders(case) if pending_actions(case, u)] + [case.author]
    if case.is_terminal:
        people.append(case.advisor)
    actor = str(event.actor) if event.actor else "سامانه"
    happened = f"«{wf.action_label(event.action)}» توسط {actor} انجام شد."
    deadline = wf.next_deadline(case)
    facts = {"what_happened": happened,
             "next_deadline": _deadline_facts(deadline, today) if deadline else None}
    subject = f"[سامانه پروژه] {case.get_state_display()} — {case.title_fa}"
    sentences = [f"در پرونده‌ی «{case.title_fa}»، {happened}"]
    if deadline:
        sentences.append(f"نزدیک‌ترین مهلت: {deadline.get_kind_display()} — "
                         f"{to_jalali(deadline.due_date)} ({days_phrase(facts['next_deadline']['days_left'])}).")
    return [_notification(f"event:{event.pk}:{u.pk}", u, case, subject, facts, sentences)
            for u in _unique(people) if u.is_active and u.email and u.pk != event.actor_id]


def notify_event(event):
    """اعلان تغییر وضعیت را در صف می‌گذارد و به گردش‌کار notify-event در n8n می‌سپارد."""
    if event.from_state == event.to_state:
        return []
    items = build_event_notifications(event)
    if not items:
        return []
    recipients = {int(i["key"].rsplit(":", 1)[1]): i for i in items}
    Notification.objects.bulk_create(
        [Notification(case=event.case, recipient_id=pk, kind=Notification.Kind.EVENT, event=event,
                      subject=item["subject"]) for pk, item in recipients.items()],
        ignore_conflicts=True)
    try:
        n8n.post("notify-event", {"notifications": items})
    except n8n.Unavailable as e:
        log.warning("notify-event: %s", e)
        Notification.objects.filter(event=event, status=Notification.Status.QUEUED).update(
            status=Notification.Status.FAILED)
    return items


def due_reminders(today=None):
    """
    یادآوری‌هایی که «امروز» باید فرستاده شوند — کاملاً قاعده‌محور و بدون هوش مصنوعی
    (مبنای ارزیابی سطح ۲: صحت ردیابی مهلت).
      • مهلت‌های باز پرونده‌های فعال (به‌جز «زودترین تاریخ دفاع» که سررسید نیست)
      • مرحله‌ی یادآوری از rules.reminder_stage: ۱۴، ۷، ۳، ۱ روز مانده، روز مهلت، و سپس هفتگی
      • گیرندگان: هر که اکنون نوبت اقدام اوست + نویسنده
      • هر (مهلت، مرحله، گیرنده) فقط یک بار: آن‌چه قبلاً «ارسال‌شده» ثبت شده دوباره نمی‌آید
    گردش‌کار n8n هر روز این فهرست را می‌گیرد، متن را می‌نویسد، ایمیل می‌زند و نتیجه را ثبت می‌کند.
    """
    today = today or timezone.localdate()
    deadlines = (Deadline.objects.filter(satisfied_at__isnull=True)
                 .exclude(kind=DeadlineKind.DEFENSE_EARLIEST)
                 .exclude(case__state__in=sm.TERMINAL_STATES)
                 .select_related("case__author", "case__advisor", "case__department")
                 .order_by("due_date", "id"))
    items = []
    for deadline in deadlines:
        facts = _deadline_facts(deadline, today)
        stage = rules.reminder_stage(facts["days_left"])
        if stage is None:
            continue
        case = deadline.case
        already = set(Notification.objects.filter(deadline=deadline, stage=stage,
                                                  status=Notification.Status.SENT)
                      .values_list("recipient_id", flat=True))
        people = [u for u in stakeholders(case) if pending_actions(case, u)] + [case.author]
        subject = f"[یادآوری مهلت] {facts['label']} — {facts['status']}"
        sentences = [f"مهلت «{facts['label']}» برای پرونده‌ی «{case.title_fa}» "
                     f"{facts['date']} است ({facts['status']})."]
        items += [_notification(f"reminder:{deadline.pk}:{stage}:{u.pk}", u, case, subject,
                                {"deadline": facts}, sentences)
                  for u in _unique(people) if u.is_active and u.email and u.pk not in already]
    return items


def record_notification(data):
    """
    نتیجه‌ی ارسال یک اعلان از n8n: {"key", "status": "sent"|"failed", "subject", "body", "generated_by"}
    key همان کلیدی است که پورتال ساخته بود: event:<رویداد>:<گیرنده> یا reminder:<مهلت>:<مرحله>:<گیرنده>
    """
    parts = str(data.get("key") or "").split(":")
    sent = data.get("status") == "sent"
    fields = {"subject": str(data.get("subject") or "")[:250], "body": str(data.get("body") or ""),
              "generated_by": str(data.get("generated_by") or "")[:20],
              "status": Notification.Status.SENT if sent else Notification.Status.FAILED,
              "sent_at": timezone.now() if sent else None}
    try:
        recipient = User.objects.get(pk=int(parts[-1]))
        if parts[0] == "event" and len(parts) == 3:
            event = CaseEvent.objects.get(pk=int(parts[1]))
            lookup = {"event": event, "recipient": recipient}
            fields.update(case=event.case, kind=Notification.Kind.EVENT)
        elif parts[0] == "reminder" and len(parts) == 4:
            deadline = Deadline.objects.get(pk=int(parts[1]))
            lookup = {"deadline": deadline, "stage": parts[2][:20], "recipient": recipient}
            fields.update(case=deadline.case, kind=Notification.Kind.REMINDER)
        else:
            raise ValueError(parts)
    except (ValueError, User.DoesNotExist, CaseEvent.DoesNotExist, Deadline.DoesNotExist):
        raise AgentError("کلید اعلان نامعتبر است.") from None
    notification, _ = Notification.objects.update_or_create(**lookup, defaults=fields)
    return notification


def visible_notifications(case, user):
    """کارشناس آموزش همه‌ی اعلان‌های پرونده را می‌بیند؛ بقیه فقط اعلان‌های خودشان را."""
    notifications = case.notifications.select_related("recipient")
    if user.is_superuser or user.user_type == UserType.EDU_STAFF:
        return notifications
    return notifications.filter(recipient=user)


# ───────────────────────── اتصال به رویدادهای پرونده ─────────────────────────

def _validate_on_upload(event):
    if event.action == f"upload_{DocKind.PROPOSAL}":
        version = DocumentVersion.objects.filter(pk=event.data.get("version_id")).first()
        if version:
            request_validation(version)


def _suggest_on_assignment(event):
    if event.to_state == State.REFEREE_ASSIGNMENT and event.from_state != event.to_state:
        request_referee_suggestions(event.case)


def handle_event(event_id):
    """
    پس از ثبت قطعی هر CaseEvent فراخوانی می‌شود (signals.py) و عامل‌های مرتبط را خبر می‌کند.
    هر عامل جدا اجرا می‌شود تا خطای یکی جلوی بقیه را نگیرد؛ هیچ خطایی به کاربر پورتال نمی‌رسد.
    """
    event = CaseEvent.objects.select_related("case__author", "case__advisor", "case__department",
                                             "actor").get(pk=event_id)
    for step in (_validate_on_upload, _suggest_on_assignment, notify_event):
        try:
            step(event)
        except Exception:
            log.exception("agent step %s failed for event %s", step.__name__, event_id)
