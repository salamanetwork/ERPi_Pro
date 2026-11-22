from django.db import migrations


def seed(apps, schema_editor):
    Stage = apps.get_model("crm", "Stage")
    for order, (code, en, ar, kind) in enumerate((
        ("new", "New", "جديدة", "open"),
        ("qualified", "Qualified", "مؤهلة", "open"),
        ("proposal", "Proposal", "عرض", "open"),
        ("negotiation", "Negotiation", "تفاوض", "open"),
        ("won", "Won", "ناجحة", "won"),
        ("lost", "Lost", "مفقودة", "lost"),
    ), start=1):
        Stage.objects.get_or_create(code=code, defaults=dict(name_en=en, name_ar=ar, kind=kind, order=order * 10))
    apps.get_model("crm", "CustomerNumber").objects.get_or_create(pk=1)


class Migration(migrations.Migration):
    dependencies = [("crm", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
