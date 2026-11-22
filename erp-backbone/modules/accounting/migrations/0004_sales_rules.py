from datetime import date

from django.db import migrations


def seed_sales(apps, schema_editor):
    db = schema_editor.connection.alias
    ledger = apps.get_model("accounting", "Ledger").objects.using(db).get(pk=1)
    accounts = apps.get_model("accounting", "Account").objects.using(db)
    rules = apps.get_model("accounting", "Rule").objects.using(db)


    base = rules.filter(ledger=ledger, kind="receipt").order_by("starts").first()
    assets, _ = accounts.get_or_create(ledger=ledger, code="SALES-ASSETS",
        defaults={"name": "Sales assets", "kind": "asset", "detail": False})
    revenue, _ = accounts.get_or_create(ledger=ledger, code="SALES-INCOME",
        defaults={"name": "Sales income", "kind": "revenue", "detail": False})
    expenses, _ = accounts.get_or_create(ledger=ledger, code="SALES-COSTS",
        defaults={"name": "Sales costs", "kind": "expense", "detail": False})

    def add(code, name, parent, control=True):
        account, _ = accounts.get_or_create(ledger=ledger, code=code, defaults={
            "name": name, "parent": parent, "kind": parent.kind, "detail": True, "control": control})
        return account

    unbilled = add("SALES-UNB", "Unbilled sales", assets)
    ar = add("SALES-AR", "Accounts receivable", assets)
    cash = accounts.filter(ledger=ledger, code="1100", kind="asset", detail=True, active=True).first()
    if cash is None:
        cash = add("SALES-BANK", "Customer receipts bank", assets, False)
    income = add("SALES-REV", "Sales revenue", revenue)
    cost = add("SALES-COGS", "Cost of goods sold", expenses)
    for kind in ("delivery", "invoice", "receipt", "return", "credit"):
        rules.get_or_create(ledger=ledger, kind="sales_" + kind, starts=date(1900, 1, 1), defaults={
            "inventory": cash if kind == "receipt" else base.inventory,
            "offset": ar if kind in ("invoice", "credit") else unbilled,
            "variance": cost, "revenue": income, "created_by": "system"})


class Migration(migrations.Migration):
    dependencies = [("accounting", "0003_posting_sale_rule_revenue_alter_posting_movement_and_more")]
    operations = [migrations.RunPython(seed_sales, migrations.RunPython.noop)]
