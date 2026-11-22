from django.db import migrations


def seed_common_units(apps, schema_editor):
    units = apps.get_model("inventory", "Unit").objects.using(schema_editor.connection.alias)
    for code, name in (("PC", "Piece"), ("KG", "Kilogram"), ("BOX", "Box")):
        units.get_or_create(code=code, defaults={"name": name})


class Migration(migrations.Migration):
    dependencies = [("inventory", "0001_initial")]
    operations = [migrations.RunPython(seed_common_units, migrations.RunPython.noop)]
