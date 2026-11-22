from django.db import migrations


def initialize_modules(apps, schema_editor):
    states = apps.get_model("backbone", "ModuleState").objects
    enabled = set(states.filter(enabled=True).values_list("module_id", flat=True))
    for module, parent in (
        ("suppliers", "purchasing"), ("purchase_orders", "purchasing"),
        ("supplier_invoices", "accounting"), ("customers", "sales"),
    ):
        states.get_or_create(module_id=module, defaults={"enabled": parent in enabled and (module != "supplier_invoices" or
                                     {"purchasing", "inventory"} <= enabled)})


class Migration(migrations.Migration):
    dependencies = [("backbone", "0010_navigationsettings_profilepicture")]
    operations = [migrations.RunPython(initialize_modules, migrations.RunPython.noop)]
