from django.db import migrations


def seed(apps, schema_editor):
    accounts = apps.get_model("accounting", "Account").objects.using(schema_editor.connection.alias)
    for code, name, kind in (
        ("PAY-BASIC", "Basic salary expense", "expense"), ("PAY-ALLOW", "Payroll allowances", "expense"),
        ("PAY-EXTRA", "Payroll variable earnings", "expense"), ("PAY-NET", "Net wages payable", "liability"),
        ("PAY-DEDUCT", "Payroll deductions clearing", "liability"),
    ):
        parent = accounts.filter(ledger_id=1, kind=kind, detail=False,
                                 code="5000" if kind == "expense" else "2000").first()
        accounts.get_or_create(ledger_id=1, code=code, defaults={"name": name, "kind": kind,
                                                                "detail": True, "control": True, "parent": parent})


class Migration(migrations.Migration):
    dependencies = [("hr", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
