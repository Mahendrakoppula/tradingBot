"""Equity-protection capital tiers (spec: Rs.50k normal -> Rs.47.5k
reduced -> Rs.45k defensive -> Rs.42.5k stop-live/research-only).
Implemented as percentage-of-starting-capital drawdown tiers (5%/10%/15%)
rather than hardcoded rupee figures, so this scales correctly if the
configured starting capital ever differs from the spec's Rs.50,000
illustration - the spec's own thresholds are exactly 5% steps off
Rs.50,000, so this is a generalization of the same intent, not a
different rule.
"""
from dataclasses import dataclass

REDUCED_DRAWDOWN_PCT = -0.05
DEFENSIVE_DRAWDOWN_PCT = -0.10
STOP_DRAWDOWN_PCT = -0.15

TIER_SIZE_MULTIPLIER = {"NORMAL": 1.0, "REDUCED": 0.5, "DEFENSIVE": 0.25, "STOP": 0.0}


@dataclass
class EquityProtectionTier:
    tier: str  # "NORMAL" | "REDUCED" | "DEFENSIVE" | "STOP"
    drawdown_pct: float  # negative = a loss from starting capital
    size_multiplier: float
    allow_new_trades: bool


def classify_equity_tier(current_capital: float, starting_capital: float) -> EquityProtectionTier:
    if starting_capital <= 0:
        raise ValueError("starting_capital must be positive")

    drawdown_pct = (current_capital - starting_capital) / starting_capital

    if drawdown_pct <= STOP_DRAWDOWN_PCT:
        tier = "STOP"
    elif drawdown_pct <= DEFENSIVE_DRAWDOWN_PCT:
        tier = "DEFENSIVE"
    elif drawdown_pct <= REDUCED_DRAWDOWN_PCT:
        tier = "REDUCED"
    else:
        tier = "NORMAL"

    return EquityProtectionTier(
        tier=tier,
        drawdown_pct=drawdown_pct,
        size_multiplier=TIER_SIZE_MULTIPLIER[tier],
        allow_new_trades=(tier != "STOP"),
    )
