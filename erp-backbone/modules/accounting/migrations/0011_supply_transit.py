from django.db import migrations


def seed(apps, schema_editor):
    database = schema_editor.connection.alias
    accounts = apps.get_model("accounting", "Account").objects.using(database)
    rules = apps.get_model("accounting", "Rule").objects.using(database)
    for receipt in rules.filter(kind="receipt").order_by("pk").iterator():
        ledger = receipt.ledger_id
        transit, _ = accounts.get_or_create(ledger_id=ledger, code="INV-TRANSIT", defaults={
            "name": "Inventory in transit", "kind": "asset", "detail": True, "control": True,
        })
        loss, _ = accounts.get_or_create(ledger_id=ledger, code="TRANSFER-LOSS", defaults={
            "name": "Transfer shortage and damage", "kind": "expense", "detail": True, "control": True,
        })
        if (transit.kind, transit.detail, loss.kind, loss.detail) != ("asset", True, "expense", True):
            continue
        rules.get_or_create(ledger_id=ledger, kind="transfer_out", starts=receipt.starts, defaults={
            "inventory_id": receipt.inventory_id, "offset": transit, "variance": loss, "created_by": "system",
        })


class Migration(migrations.Migration):
    dependencies = [("accounting", "0010_dashboard_date_indexes")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
