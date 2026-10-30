"""Estimate settlement from seller-supplied product terms, never as platform fact."""
from decimal import Decimal, DecimalException, InvalidOperation, ROUND_HALF_UP

CENT = Decimal('0.01')
HUNDRED = Decimal(100)

def _number(value):
    try:
        result = Decimal(str(value).strip().replace('¥', '').replace('￥', '').replace(',', ''))
        return result if result.is_finite() and result >= 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None

def estimate_settlement(amount, quantity, rule):
    """Return a rounded Decimal, or None for missing/invalid inputs."""
    if not rule:
        return None
    mode, fixed_unit, coin_rate, worry_rate, fee_order = rule
    if mode == 'fixed':
        unit = _number(fixed_unit)
        try:
            qty = int(quantity)
        except (TypeError, ValueError, OverflowError):
            return None
        if unit is None or qty < 1:
            return None
        try:
            return (unit * qty).quantize(CENT, rounding=ROUND_HALF_UP)
        except DecimalException:
            return None
    amount, coin, worry = _number(amount), _number(coin_rate), _number(worry_rate)
    if mode != 'percent' or amount is None or coin is None or worry is None or coin > 100 or worry > 100:
        return None
    if worry > 0 and fee_order not in ('parallel', 'sequential'):
        return None
    if fee_order == 'parallel' and coin + worry > HUNDRED:
        return None
    try:
        if fee_order == 'sequential':
            value = amount * (HUNDRED - coin) / HUNDRED * (HUNDRED - worry) / HUNDRED
        else:
            value = amount * (HUNDRED - coin - worry) / HUNDRED
        return value.quantize(CENT, rounding=ROUND_HALF_UP)
    except DecimalException:
        return None
