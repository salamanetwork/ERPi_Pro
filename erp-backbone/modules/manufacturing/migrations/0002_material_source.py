from django.db import migrations, models
from django.db.models import OuterRef, Subquery
import django.db.models.deletion


def inherit_order_source(apps, schema_editor):
    material = apps.get_model("manufacturing", "Material")
    order = apps.get_model("manufacturing", "Order")
    database = schema_editor.connection.alias
    source = order.objects.using(database).filter(pk=OuterRef("order_id")).values("source_id")
    material.objects.using(database).update(source_id=Subquery(source))


class Migration(migrations.Migration):
    dependencies = [("manufacturing", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="material", name="source",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name="+", to="inventory.location"),
        ),
        migrations.RunPython(inherit_order_source, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="material", name="source",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                    related_name="+", to="inventory.location"),
        ),
    ]
