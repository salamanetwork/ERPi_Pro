from django.db import migrations
from datetime import date

CHART = (
    ("1000", "Assets", "asset", None, False, False),
    ("1100", "Cash and bank", "asset", "1000", True, False),
    ("1200", "Inventory", "asset", "1000", True, True),
    ("2000", "Liabilities", "liability", None, False, False),
    ("2100", "Goods received not invoiced (GRNI)", "liability", "2000", True, True),
    ("3000", "Equity", "equity", None, False, False),
    ("3100", "Opening inventory offset", "equity", "3000", True, True),
    ("4000", "Revenue", "revenue", None, False, False),
    ("4100", "Other revenue", "revenue", "4000", True, False),
    ("5000", "Expenses", "expense", None, False, False),
    ("5100", "Return valuation difference", "expense", "5000", True, True),
    ("5200", "Other expense", "expense", "5000", True, False),
)
KINDS = ("receipt", "return", "correction", "opening")


def seed(apps, schema_editor):
    db = schema_editor.connection.alias
    ledger, _ = apps.get_model("accounting", "Ledger").objects.using(db).get_or_create(pk=1)
    accounts = {}
    for code, name, kind, parent, detail, control in CHART:
        accounts[code], _ = apps.get_model("accounting", "Account").objects.using(db).get_or_create(
            ledger=ledger, code=code,
            defaults={"name": name, "kind": kind, "parent": accounts.get(parent), "detail": detail, "control": control},
        )
    for kind in KINDS:
        apps.get_model("accounting", "Rule").objects.using(db).get_or_create(
            ledger=ledger, kind=kind, starts=date(1900, 1, 1),
            defaults={"inventory": accounts["1200"], "offset": accounts["3100" if kind == "opening" else "2100"],
                      "variance": accounts["5100"] if kind == "return" else None, "created_by": "system"},
        )


class Migration(migrations.Migration):
    dependencies = [("accounting", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
