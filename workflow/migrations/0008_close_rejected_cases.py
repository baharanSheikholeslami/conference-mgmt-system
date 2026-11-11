from django.db import migrations

OLD, NEW = "rejected", "cancelled"


def forward(apps, schema_editor):
    """
    وضعیت «ردشده» حذف شد: رد در شورا اکنون پرونده را به تدوین پیشنهاد برمی‌گرداند.
    پرونده‌هایی که پیش از این تغییر رد شده بودند بسته می‌مانند و «لغوشده» می‌شوند؛ رویداد council_reject
    و توضیح آن در تاریخچه می‌ماند. (بازگشایی خودکار ممکن نیست: دانشجو شاید پرونده‌ی تازه‌ای ساخته باشد.)
    """
    ProjectCase = apps.get_model("workflow", "ProjectCase")
    CaseEvent = apps.get_model("workflow", "CaseEvent")
    ProjectCase.objects.filter(state=OLD).update(state=NEW)
    # CaseEvent فقط‌افزودنی است (save/delete در مدل واقعی ممنوع)؛ مدل تاریخی در مهاجرت این قید را ندارد
    CaseEvent.objects.filter(from_state=OLD).update(from_state=NEW)
    CaseEvent.objects.filter(to_state=OLD).update(to_state=NEW)


class Migration(migrations.Migration):
    dependencies = [("workflow", "0007_defense_request_and_time_picking")]
    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
