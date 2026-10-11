# راهنمای کامل نصب و اجرا

این راهنما سامانه را از صفر روی یک مک با تراشه‌ی اپل (مثلاً MacBook Pro M2 Pro با ۱۶ گیگابایت رم) راه می‌اندازد: پورتال جنگو، پایگاه‌داده، n8n، صندوق ایمیل آزمایشی و مدل‌های زبانی محلی با Ollama.

## ۰. نمای کلی

| جزء | کجا اجرا می‌شود | نشانی |
|---|---|---|
| پورتال (جنگو) | روی خود مک، داخل `venv` | <http://localhost:8000> |
| پنل ادمین | همان پورتال | <http://localhost:8000/admin> |
| Postgres + pgvector | داکر | `localhost:5432` |
| n8n (چهار گردش‌کار عامل‌ها) | داکر | <http://localhost:5678> |
| Mailpit (صندوق ایمیل آزمایشی) | داکر | <http://localhost:8025> |
| Ollama (مدل گفتگو + مدل embedding) | روی خود مک (برای دسترسی به GPU) | `localhost:11434` |

Ollama عمداً بیرون از داکر اجرا می‌شود، چون داکرِ مک به GPU تراشه‌ی اپل دسترسی ندارد. n8n از درون داکر با نشانی `host.docker.internal` به آن وصل می‌شود.

## ۱. پیش‌نیازها (یک‌بار)

```bash
# Homebrew (اگر ندارید): https://brew.sh
brew install python@3.12 git
brew install --cask docker      # Docker Desktop؛ یک‌بار باز کنید تا راه بیفتد
brew install ollama             # یا برنامه‌ی Ollama را از https://ollama.com نصب کنید
```

بررسی:

```bash
python3.12 --version && docker --version && ollama --version
```

## ۲. مدل‌های زبانی با Ollama

### ۲-۱. روشن‌کردن Ollama

اگر برنامه‌ی Ollama را نصب کرده‌اید فقط آن را باز کنید (آیکونش در نوار بالای صفحه می‌آید). اگر با brew نصب کرده‌اید:

```bash
brew services start ollama      # همیشه در پس‌زمینه روشن بماند
```

### ۲-۲. دانلود مدل‌ها

```bash
ollama pull gemma3:4b     # مدل گفتگو، حدود ۳٫۳ گیگابایت (پیشنهادی)
ollama pull bge-m3        # مدل embedding چندزبانه، حدود ۱٫۲ گیگابایت (لازم)
ollama list               # هر دو باید در فهرست باشند
```

**کدام مدل گفتگو؟** با ۱۶ گیگابایت رم، داکر (حدود ۲ تا ۴ گیگابایت) و دو مدل هم‌زمان جا می‌شوند:

| مدل | حجم | توضیح |
|---|---|---|
| `gemma3:4b` | ۳٫۳ GB | پیشنهادی؛ فارسی خوب و سرعت مناسب |
| `qwen2.5:7b` | ۴٫۷ GB | دقیق‌تر و کندتر؛ اگر رم آزاد دارید |
| `gemma3:1b` | ۰٫۸ GB | فقط برای آزمودن اتصال. در اجرای زنده‌ی فاز ۴، پیشنهاد استاد و داور و اعلان‌ها را درست انجام داد، اما در بررسی قالب سند هر بار فقط یک بخش را تشخیص داد |

مدل‌های بزرگ‌تر از ۸ میلیارد پارامتر روی ۱۶ گیگابایت کند می‌شوند و توصیه نمی‌شوند.

> اگر `ollama pull` با خطای `operation timed out` روی `registry.ollama.ai` متوقف شد، دسترسی شبکه به مخزن Ollama بسته است. با VPN دوباره امتحان کنید. پس از دانلود، اجرای مدل به اینترنت نیاز ندارد.

آزمایش سریع:

```bash
ollama run gemma3:4b "در یک جمله بگو RAG چیست."
```

## ۳. دریافت کد و محیط پایتون

```bash
git clone https://github.com/baharanSheikholeslami/conference-mgmt-system.git
cd conference-mgmt-system
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

از این به بعد هر ترمینال تازه‌ای که باز می‌کنید، اول `cd` به پوشه‌ی پروژه بروید و `source venv/bin/activate` را بزنید.

## ۴. تنظیمات (`.env`)

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(50))"   # برای DJANGO_SECRET_KEY
python -c "import secrets; print(secrets.token_urlsafe(32))"   # برای AGENT_TOKEN
```

فایل `.env` را باز کنید و این‌ها را تنظیم کنید:

| متغیر | مقدار |
|---|---|
| `DJANGO_SECRET_KEY` | رشته‌ی اول |
| `AGENT_TOKEN` | رشته‌ی دوم (راز مشترک پورتال و n8n) |
| `AGENTS_ENABLED` | `True` (با `False` پورتال بدون هوش مصنوعی و دقیقاً مثل فاز ۲ کار می‌کند) |
| `LLM_CHAT_MODEL` | نام مدلی که در بخش ۲ دانلود کردید، مثلاً `gemma3:4b` |
| `EMBEDDING_MODEL` | `bge-m3` |
| `AGENTS_SHADOW_MODE` | `False` (برای اجرای سایه در بخش ۹ `True` کنید) |

بقیه‌ی مقادیر پیش‌فرض برای اجرا روی همین مک درست‌اند. اطلاعات پایگاه‌داده در `.env` باید با `docker-compose.yml` یکی باشد (`dev` / `devpass` / `conference_db`).

> هر بار `.env` را عوض کردید، `docker compose up -d` را دوباره بزنید تا n8n مقادیر تازه را بگیرد، و سرور جنگو را هم از نو اجرا کنید.

## ۵. سرویس‌های داکر

```bash
docker compose up -d
docker compose ps        # سه سرویس db، n8n و mailpit باید Up باشند
```

بار اول <http://localhost:5678> را باز کنید و حساب مالک n8n را بسازید (ایمیل و رمز دلخواه؛ فقط روی همین مک است).

## ۶. پایگاه‌داده و داده‌ی نمونه

```bash
python manage.py migrate
python manage.py seed_demo            # کاربران و سه پرونده‌ی نمونه
python manage.py createsuperuser      # حساب ادمین خودتان
```

کاربران نمونه (رمز همه `test1234`):

| نام کاربری | نقش |
|---|---|
| `student1`، `student2`، `student3` | دانشجو (`student2` پرونده ندارد؛ برای آزمودن از صفر) |
| `external1` | نویسنده غیردانشجو |
| `advisor1` | استاد راهنما |
| `referee1`، `referee2` | داور |
| `head1` | مدیر گروه |
| `edu1` | کارشناس آموزش دانشکده |

## ۷. گردش‌کارهای n8n

```bash
bash n8n/import.sh
```

این دستور اعتبارنامه‌ی ایمیل (Mailpit) و چهار گردش‌کار را وارد و فعال می‌کند و n8n را دوباره راه می‌اندازد. در <http://localhost:5678> هر چهار گردش‌کار باید Published یا Active باشند. اگر دستور خطا داد، روش دستی در `docs/phase3.md` بخش ۴-۳ آمده است.

## ۸. اجرای پورتال و بررسی اتصال‌ها

ترمینال ۱ (این ترمینال باز می‌ماند):

```bash
python manage.py runserver 0.0.0.0:8000
```

ترمینال ۲:

```bash
python manage.py index_policy         # نمایه‌سازی پایگاه دانش دستیار (پوشه‌ی agents/knowledge)
python manage.py agents_check --ask "مهلت تحویل پیشنهاد پروژه چقدر است؟"
```

`agents_check` همه‌ی حلقه‌ها را گام‌به‌گام می‌آزماید: تنظیمات، n8n، دسترسی n8n به پورتال، وجود مدل‌ها در Ollama، مدل embedding، پایگاه دانش و در پایان یک پرسش واقعی از دستیار. در پایان باید «همه‌چیز آماده است» ببینید.

اگر فایل رسمی رویه‌ی AUT-PR-3210 را دارید، آن را هم نمایه کنید:

```bash
python manage.py index_policy ~/Downloads/AUT-PR-3210.pdf --code AUT-PR-3210 \
    --title "چگونگی ثبت‌نام، تصویب، و دفاع از پایان‌نامه در مقطع کارشناسی"
```

حالا <http://localhost:8000> را باز کنید و با یکی از کاربران نمونه وارد شوید. ایمیل‌های اعلان در <http://localhost:8025> دیده می‌شوند.

## ۹. آزمون‌ها و سناریوهای سرتاسری (فاز ۴)

```bash
python manage.py test                       # همه‌ی آزمون‌های خودکار (به n8n و مدل نیاز ندارد)
python manage.py run_scenarios --list       # فهرست هفت سناریو
python manage.py run_scenarios              # اجرای آفلاین هر هفت سناریو؛ چیزی در پایگاه‌داده نمی‌ماند
python manage.py run_scenarios --live --report docs/reports   # اجرای زنده با n8n و مدل زبانی
```

اجرای زنده به روشن بودن همه‌چیز (بخش ۵ تا ۸) نیاز دارد. با `gemma3:4b` چند دقیقه طول می‌کشد. گزارش Markdown و JSON آن در `docs/reports` ذخیره می‌شود. برای دیدن پرونده‌های سناریو در پورتال `--keep` را اضافه کنید و بعد با `python manage.py run_scenarios --cleanup` پاکشان کنید.

**اجرای سایه (Shadow Mode):** در `.env` بنویسید `AGENTS_SHADOW_MODE=True`، سرور را از نو اجرا و `docker compose up -d` را بزنید. از این پس عامل‌ها کار می‌کنند ولی فقط `edu1` و ادمین خروجی‌شان را می‌بینند و همه‌ی ایمیل‌ها به `shadow@example.com` می‌روند. پس از یک دوره کار واقعی:

```bash
python manage.py shadow_report --output docs/reports/shadow.md
```

جزئیات فاز ۴ در `docs/phase4.md` آمده است.

## ۱۰. کار روزانه

روشن‌کردن:

```bash
cd ~/conference-mgmt-system && source venv/bin/activate
docker compose up -d                       # Ollama هم باید روشن باشد
python manage.py runserver 0.0.0.0:8000
```

خاموش‌کردن: در ترمینال سرور `Ctrl+C` و سپس `docker compose stop`. داده‌ها در volumeهای داکر می‌مانند.

پس از `git pull` اگر کد تازه آمده است:

```bash
pip install -r requirements.txt
python manage.py migrate
bash n8n/import.sh            # اگر فایل‌های پوشه‌ی n8n/workflows عوض شده‌اند
```

## ۱۱. استفاده از مدل ابری به‌جای Ollama (اختیاری)

هر سرویس سازگار با OpenAI کار می‌کند. در `.env` این سه را عوض کنید و `docker compose up -d` بزنید:

```
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=کلید-شما
LLM_CHAT_MODEL=gpt-4o-mini
```

مدل embedding می‌تواند همچنان `bge-m3` روی Ollama بماند. در این صورت `EMBEDDING_BASE_URL=http://host.docker.internal:11434/v1` و `EMBEDDING_API_KEY=ollama` را هم بنویسید. اگر مدل embedding را عوض کردید، `index_policy` را دوباره اجرا کنید.

## ۱۲. عیب‌یابی

| نشانه | راه‌حل |
|---|---|
| `template database "template1" has a collation version mismatch` هنگام `manage.py test` | `docker compose exec db psql -U dev -d postgres -c "ALTER DATABASE template1 REFRESH COLLATION VERSION"` و همین را برای `postgres` و `conference_db` |
| n8n هنوز مدل یا تنظیمات قبلی را به کار می‌برد | `docker compose up -d` (فقط restart کافی نیست؛ متغیرهای `.env` هنگام ساختن کانتینر خوانده می‌شوند) |
| همه‌ی عامل‌ها «بی‌پاسخ» می‌مانند یا `ECONNREFUSED …:8000` در `docker compose logs n8n` | سرور جنگو خاموش است. اجرا کنید `python manage.py runserver 0.0.0.0:8000` |
| `agents_check` می‌گوید مدلی در Ollama نیست | `ollama pull <نام مدل>`؛ نام باید دقیقاً با `.env` یکی باشد |
| `ollama pull` با timeout متوقف می‌شود | دسترسی به `registry.ollama.ai` بسته است؛ با VPN دوباره امتحان کنید |
| اعتبارسنجی سند تقریباً همه‌ی بخش‌ها را «پیدا نشد» می‌گوید | مدل گفتگو خیلی کوچک است (مثلاً `gemma3:1b`)؛ به `gemma3:4b` یا `qwen2.5:7b` بروید |
| خطای `HTTP 404` از Webhook | گردش‌کار فعال نیست: `bash n8n/import.sh` |
| خطای `Unauthorized` در اجرای n8n | `AGENT_TOKEN` پس از تغییر به کانتینر نرسیده: `docker compose up -d` |
| ایمیلی در Mailpit نیست | در n8n بخش Executions گردش‌کار «۴. اطلاع‌رسانی» را ببینید؛ در پورتال وضعیت اعلان «ناموفق» نشان داده می‌شود |
| دستیار می‌گوید پایگاه دانش نمایه نشده | `python manage.py index_policy` |

جدول کامل‌تر عیب‌یابی عامل‌ها در `docs/phase3.md` بخش ۶ است.
