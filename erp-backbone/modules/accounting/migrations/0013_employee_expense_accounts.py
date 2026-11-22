from django.db import migrations

def seed(apps, schema_editor):
    accounts=apps.get_model("accounting","Account").objects
    for code,name,kind,parent in (
        ("EMP-PAY","Employee reimbursements payable","liability","2000"),
        ("EXP-TAX","Recoverable employee expense tax","asset","1000"),
        ("EMP-EXP","Employee expenses","expense","5000"),
    ):
        accounts.get_or_create(ledger_id=1,code=code,defaults=dict(name=name,kind=kind,detail=True,control=True,parent=accounts.get(ledger_id=1,code=parent)))

class Migration(migrations.Migration):
    dependencies=[("accounting","0012_manufacturing_accounts")]
    operations=[migrations.RunPython(seed,migrations.RunPython.noop)]
