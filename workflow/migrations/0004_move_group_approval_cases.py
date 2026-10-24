from django.db import migrations

OLD, NEW = "group_approval", "council_approval"


def forward(apps, schema_editor):
    """مرحله‌ی «تصویب گروه» حذف شد؛ پرونده‌های آن‌جا و تاریخچه‌شان به مرحله‌ی بعد (شورا) می‌روند."""
    ProjectCase = apps.get_model("workflow", "ProjectCase")
    CaseEvent = apps.get_model("workflow", "CaseEvent")
    ProjectCase.objects.filter(state=OLD).update(state=NEW)
    # CaseEvent فقط‌افزودنی است (save/delete در مدل واقعی ممنوع)؛ مدل تاریخی در مهاجرت این قید را ندارد
    CaseEvent.objects.filter(from_state=OLD).update(from_state=NEW)
    CaseEvent.objects.filter(to_state=OLD).update(to_state=NEW)


class Migration(migrations.Migration):
    dependencies = [("workflow", "0003_advisor_request_no_group_step")]
    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
