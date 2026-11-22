from django.db import migrations


def seed(apps, schema_editor):
    accounts = apps.get_model("accounting", "Account").objects.using(schema_editor.connection.alias)
    for code, name, kind in (
        ("AP-CONTROL", "Supplier payables", "liability"),
        ("AP-RETURNS", "Invoiced supplier returns clearing", "asset"),
    ):
        parent = accounts.filter(ledger_id=1, code="2000" if kind == "liability" else "1000",
                                 kind=kind, detail=False).first()
        accounts.get_or_create(ledger_id=1, code=code, defaults={
            "name": name, "kind": kind, "parent": parent, "detail": True, "control": True,
        })


class Migration(migrations.Migration):
    dependencies = [("accounting", "0005_supplierinvoice_invoicematch_suppliersettlement_and_more")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
