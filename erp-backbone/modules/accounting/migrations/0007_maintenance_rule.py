from django.db import migrations


def seed(apps, schema_editor):
    db = schema_editor.connection.alias
    accounts = apps.get_model("accounting", "Account").objects.using(db)
    rules = apps.get_model("accounting", "Rule").objects.using(db)
    expense, _ = accounts.get_or_create(ledger_id=1, code="MAINT-EXP", defaults={
        "name": "Maintenance spare parts expense", "kind": "expense", "detail": True, "control": True,
        "parent": accounts.filter(ledger_id=1, code="5000", kind="expense", detail=False).first(),
    })
    for receipt in list(rules.filter(ledger_id=1, kind="receipt").order_by("starts")):
        rules.get_or_create(ledger_id=1, kind="maint_issue", starts=receipt.starts,
                            defaults={"inventory_id": receipt.inventory_id, "offset": expense, "created_by": "system"})


class Migration(migrations.Migration):
    dependencies = [("accounting", "0006_payable_accounts")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
