from django.db import migrations


def split_payroll(apps, schema_editor):
    states = apps.get_model("backbone", "ModuleState").objects
    active = set(states.filter(enabled=True).values_list("module_id", flat=True))
    states.get_or_create(module_id="payroll", defaults={"enabled": {"hr", "accounting"} <= active})

    roles = apps.get_model("backbone", "Role").objects
    for role in roles.iterator():
        hidden = role.hidden_modules
        if "hr" in hidden and "payroll" not in hidden:
            role.hidden_modules = [*hidden, "payroll"]
            role.save(update_fields=["hidden_modules"])


class Migration(migrations.Migration):
    dependencies = [("backbone", "0011_procurement_sales_modules")]
    operations = [migrations.RunPython(split_payroll, migrations.RunPython.noop)]
