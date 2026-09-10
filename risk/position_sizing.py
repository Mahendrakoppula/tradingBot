"""Risk-based position sizing - rounds DOWN to whole lots, never up past
the risk budget (a partial lot doesn't exist; rounding up would silently
risk more than intended). Deliberately takes stop distance in
underlying POINTS, not rupees, and a separate slippage buffer in points -
spread/slippage/costs must be accounted for explicitly by the caller
supplying a realistic estimate, never assumed away, per the spec's
"never assume LTP=fill" execution-realism principle.
"""
import math


def position_size_lots(
    capital_at_risk: float,
    stop_loss_points: float,
    lot_size: int,
    estimated_slippage_points: float = 0.0,
    max_lots: int | None = None,
) -> int:
    if capital_at_risk <= 0 or lot_size <= 0:
        return 0

    effective_risk_per_unit_points = stop_loss_points + estimated_slippage_points
    if effective_risk_per_unit_points <= 0:
        return 0

    risk_per_lot = effective_risk_per_unit_points * lot_size
    lots = math.floor(capital_at_risk / risk_per_lot)

    if max_lots is not None:
        lots = min(lots, max_lots)
    return max(lots, 0)


def capital_at_risk_for_trade(current_capital: float, base_risk_pct: float, size_multiplier: float = 1.0) -> float:
    """base_risk_pct: fraction of current capital normally risked per
    trade (e.g. 0.01 = 1%). size_multiplier: from
    risk.equity_protection.classify_equity_tier - scales the risk budget
    down (never up) as the account draws down."""
    if not (0 <= size_multiplier <= 1.0):
        raise ValueError(f"size_multiplier must be in [0, 1], got {size_multiplier}")
    return current_capital * base_risk_pct * size_multiplier
