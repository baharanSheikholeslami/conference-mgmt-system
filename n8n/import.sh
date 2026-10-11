#!/usr/bin/env bash
# وارد کردن اعتبارنامه‌ی ایمیل و چهار گردش‌کار فاز ۳ در n8n و فعال (Publish) کردن آن‌ها.
# پیش‌نیاز: docker compose up -d   |   اجرا از هر جایی: bash n8n/import.sh
# اجرای دوباره بی‌خطر است: گردش‌کارهای قبلی با همان شناسه جایگزین می‌شوند.
set -euo pipefail
cd "$(dirname "$0")/.."

N8N="docker compose exec -T -u node n8n n8n"
WORKFLOWS="pm3DocValidation1 pm3RagAssistant02 pm3RefereeSuggest3 pm3Notifications04"

echo "۱) اعتبارنامه‌ی SMTP (صندوق آزمایشی Mailpit)…"
$N8N import:credentials --input=/project/n8n/credentials/smtp-mailpit.json

echo "۲) گردش‌کارها…"
$N8N import:workflow --separate --input=/project/n8n/workflows/

echo "۳) فعال‌سازی…"
for id in $WORKFLOWS; do
  # n8n نسخه‌ی ۲: publish:workflow  |  نسخه‌ی ۱: update:workflow --active=true
  $N8N publish:workflow --id="$id" || $N8N update:workflow --id="$id" --active=true
done

echo "۴) راه‌اندازی دوباره‌ی n8n تا تغییرها اعمال شود…"
docker compose restart n8n
echo "انجام شد. چند ثانیه بعد http://localhost:5678 را باز کنید: هر چهار گردش‌کار باید فعال (Published/Active) باشند."
