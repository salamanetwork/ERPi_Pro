import re
from decimal import Decimal, ROUND_HALF_UP

from backbone.http import Problem

CENT = Decimal("0.01")


def decimal_value(value, whole, places, field):
    if not isinstance(value, str) or not re.fullmatch(rf"[0-9]{{1,{whole}}}(?:\.[0-9]{{1,{places}}})?", value):
        raise Problem(f"invalid_{field}", field=field,
                      reason="required" if value in (None, "") else "invalid_number")
    return Decimal(value)


def line_amount(quantity, price):
    return (quantity * price).quantize(CENT, rounding=ROUND_HALF_UP)


def check_total(total):
    if total >= Decimal("1000000000000000"):
        raise Problem("invalid_total")
