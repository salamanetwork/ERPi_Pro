from decimal import Decimal

from django.db import transaction

from accounting.models import Posting, Reference
from accounting.services import period_for, posted_entry, rule_for
from backbone.http import Problem


def originating_rule(document):
    return document.postingevent.posting.entry.rule


def cost_on_source(doc):
    return any(Decimal(line["cost"]) for line in doc.lines)


def sales_lines(ledger, doc):
    rule = rule_for(ledger, "sales_" + doc.kind, doc.date)
    amount = doc.amount
    if doc.kind == "delivery":
        stock = rule_for(ledger, "receipt", doc.date).inventory
        cost = sum((Decimal(line["cost"]) for line in doc.lines), Decimal(0))
        return rule, [(rule.offset, amount), (rule.revenue, -amount), (rule.variance, cost), (stock, -cost)]
    original = originating_rule(doc.source) if doc.source_id else None
    if doc.kind == "invoice":
        return rule, [(rule.offset, amount), (original.offset, -amount)]
    if doc.kind == "receipt":
        return rule, [(rule.inventory, amount), *[
            (originating_rule(doc.allocations[int(line["invoice_id"])]).offset, -Decimal(line["amount"]))
            for line in doc.lines]]
    if doc.kind == "credit":
        delivery = originating_rule(doc.source.source)
        return rule, [(delivery.revenue, amount), (original.offset, -amount)]
    shipped = doc.source.source.source if doc.source.kind == "credit" else doc.source
    original = originating_rule(shipped)
    stock = (shipped.postingevent.posting.entry.lines.get(amount__lt=0, account__kind="asset").account
             if cost_on_source(shipped) else original.inventory)
    cost = sum((Decimal(line["cost"]) for line in doc.lines), Decimal(0))
    return rule, [(stock, cost), (original.variance, -cost), (original.revenue, amount), (original.offset, -amount)]


def process_sales(row, actor, override, note):
    event = row.sale
    doc = event.document
    try:
        with transaction.atomic():
            day = override or doc.date
            if day < row.ledger.cutoff or day < doc.date:
                raise Problem("finance_before_cutoff", 409)
            dependencies = list(Posting.objects.filter(sale__in=event.prerequisites.all()).select_related("entry"))
            if len(dependencies) != event.prerequisites.count() or any(dep.state != "posted" for dep in dependencies):
                raise Problem("finance_sales_source_pending", 409)
            if any(day < dep.entry.date for dep in dependencies):
                raise Problem("finance_before_original", 409)
            period = period_for(row.ledger, day)
            if doc.kind == "receipt":
                from .models import Document

                doc.allocations = Document.objects.in_bulk([line["invoice_id"] for line in doc.lines])
            rule, values = sales_lines(row.ledger, doc)
            if not any(amount for _, amount in values):
                row.state, row.error = "zero", ""
                row.save(update_fields=["state", "error"])
                return row
            center = Reference.objects.filter(pk=doc.snapshot.get("cost_center_id"),
                                              ledger=row.ledger, kind="cost_center").first()
            row.entry = posted_entry(row.ledger, period, day, actor, f"Sales {doc.kind} / {doc.number}",
                doc.kind, [(account, amount, doc.site_id, center if account.kind == "expense" else None) for account, amount in values],
                source_date=doc.date, rule=rule, posting_note=note,
                original_entry=dependencies[0].entry if len(dependencies) == 1 else None)
            row.state, row.error = "posted", ""
            row.save(update_fields=["entry", "state", "error"])
    except Problem as failure:
        row.error = failure.code
        row.save(update_fields=["error"])
    return row
