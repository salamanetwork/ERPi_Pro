from decimal import Decimal

from django.utils import timezone

from backbone.http import Problem
from inventory.models import Movement
from inventory.stock import business_date

KINDS = ("transfer_out", "transfer_in", "transfer_back", "transfer_loss")


def transit_value(move):
    return -move.value if move.kind == "transfer_out" else Decimal(move.facts["transit_value"])


def loss_movement(user, settings, line, amount, facts):
    from accounting.services import ledger_lock, observe

    now = timezone.now()
    original = line.dispatch
    ledger = ledger_lock()
    move = Movement.objects.create(site_id=settings.site_id, item_id=line.line.item_id, location_id=line.line.destination_id,
        original=original, kind="transfer_loss", quantity=0, value=0, actor=user.get_username(), actor_id=user.pk,
        occurred_at=now, business_date=max(business_date(settings, now), original.business_date),
        facts={**facts, "currency": "AED", "transit_value": str(amount)})
    observe(ledger, move)
    return move


def effect(ledger, move, day):
    from accounting.models import Posting
    from accounting.services import rule_for

    amount = transit_value(move)
    if move.kind == "transfer_out":
        rule = rule_for(ledger, "transfer_out", move.business_date)
        return rule, [(rule.offset, amount, move.site_id, None), (rule.inventory, -amount, move.site_id, None)], None
    source = Posting.objects.filter(movement_id=move.original_id, ledger=ledger,
                                    state__in=("posted", "zero", "opening")).select_related("entry__rule", "movement").first()
    if source is None:
        raise Problem("finance_original_not_posted", 409)
    entry = source.entry
    if entry and day < entry.date:
        raise Problem("finance_before_original", 409)
    rule = entry.rule if entry else rule_for(ledger, "transfer_out",
                                              ledger.cutoff if source.state == "opening" else source.movement.business_date)
    account = rule.variance if move.kind == "transfer_loss" else rule.inventory
    if account is None:
        raise Problem("finance_rule_missing", 409)
    return rule, [(account, amount, move.site_id, None), (rule.offset, -amount, source.movement.site_id, None)], entry
