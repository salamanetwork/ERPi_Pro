from decimal import Decimal


def effect(ledger, movement, day):
    from accounting.models import Rule

    rule = Rule.objects.select_related("inventory","offset","variance").get(ledger=ledger, pk=movement.facts["posting_rule_id"])
    if movement.kind == "mfg_scrap":
        amount = Decimal(movement.facts["scrap_value"])
        return rule, [(rule.variance, amount, movement.site_id, None),
                      (rule.offset, -amount, movement.site_id, None)], None
    return rule, [(rule.inventory, movement.value, movement.site_id, None),
                  (rule.offset, -movement.value, movement.site_id, None)], None
