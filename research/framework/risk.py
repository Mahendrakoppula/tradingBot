"""Risk layer: adaptive stop/target placement, risk-based position sizing,
and a portfolio-level risk gate (daily loss limit, consecutive-loss
cutback, drawdown-based risk reduction, correlated-exposure cap).

This is offline/research-only and does NOT touch
trading_bot/technical_strategy.py, which stays the live bot's own
mechanism (a fixed rupee-per-lot stop/target for scalp/intraday, ATR-based
for swing - see that module's premium_stop_target/atr_stop_target). Only a
piece validated here with genuine out-of-sample evidence is a candidate
for live promotion, and that's a separate future decision.
"""
from dataclasses import dataclass

from trading_bot.support_resistance import SwingPoint


@dataclass
class StopTarget:
    stop_price: float
    target_price: float
    r_multiple_target: float  # target distance / stop distance


def atr_stop_target(
    entry_price: float, direction: str, atr_value: float, *, stop_atr_mult: float = 1.5, reward_risk_ratio: float = 2.0
) -> StopTarget:
    if direction not in ("up", "down"):
        raise ValueError(f"direction must be 'up' or 'down', got {direction!r}")
    stop_dist = atr_value * stop_atr_mult
    if direction == "up":
        stop, target = entry_price - stop_dist, entry_price + stop_dist * reward_risk_ratio
    else:
        stop, target = entry_price + stop_dist, entry_price - stop_dist * reward_risk_ratio
    return StopTarget(stop_price=stop, target_price=target, r_multiple_target=reward_risk_ratio)


def structure_stop_target(
    entry_price: float,
    direction: str,
    swing_points: list[SwingPoint],
    atr_value: float,
    *,
    reward_risk_ratio: float = 2.0,
    min_stop_atr_mult: float = 0.5,
) -> StopTarget:
    """Places the stop just beyond the most recent PROTECTIVE swing point -
    the last swing LOW for a long (a break below invalidates the setup),
    the last swing HIGH for a short - falling back to an ATR-based stop
    (via atr_stop_target) when no usable swing point exists yet, or when
    one does but it's unreasonably close (min_stop_atr_mult floor, so a
    stop tighter than that is treated as noise-prone rather than a real
    structural level)."""
    if direction not in ("up", "down"):
        raise ValueError(f"direction must be 'up' or 'down', got {direction!r}")
    fallback = atr_stop_target(entry_price, direction, atr_value, reward_risk_ratio=reward_risk_ratio)
    kind_needed = "low" if direction == "up" else "high"
    candidates = sorted((p for p in swing_points if p.kind == kind_needed), key=lambda p: p.index)
    if not candidates:
        return fallback
    level = candidates[-1].price
    stop_dist = abs(entry_price - level)
    if stop_dist < atr_value * min_stop_atr_mult:
        return fallback
    if direction == "up":
        target = entry_price + stop_dist * reward_risk_ratio
    else:
        target = entry_price - stop_dist * reward_risk_ratio
    return StopTarget(stop_price=level, target_price=target, r_multiple_target=reward_risk_ratio)


def r_multiple_price(entry_price: float, stop_price: float, direction: str, r_multiple: float) -> float:
    """The price `r_multiple` times the entry-to-stop distance away from
    entry, in the profitable direction - used for R-multiple targets and
    partial-profit tiers."""
    if direction not in ("up", "down"):
        raise ValueError(f"direction must be 'up' or 'down', got {direction!r}")
    risk_dist = abs(entry_price - stop_price)
    return entry_price + risk_dist * r_multiple if direction == "up" else entry_price - risk_dist * r_multiple


@dataclass
class PartialProfitPlan:
    tiers: tuple[tuple[float, float], ...]  # (r_multiple, fraction_of_position_to_close), ascending by r_multiple


def default_partial_profit_plan() -> PartialProfitPlan:
    return PartialProfitPlan(tiers=((1.0, 0.5), (2.0, 0.3), (3.0, 0.2)))


def chandelier_stop(
    direction: str, highest_since_entry: float, lowest_since_entry: float, atr_value: float, *, atr_mult: float = 3.0
) -> float:
    """Classic chandelier exit: trails from the most favorable extreme
    reached SINCE entry (not from current price), a wide ATR-scaled trail
    meant to ride a trend as long as possible - a deliberately different,
    slower mechanism than the live bot's tight rupee-per-lot stop, meant
    for a swing-style/trend-following position."""
    if direction == "up":
        return highest_since_entry - atr_value * atr_mult
    if direction == "down":
        return lowest_since_entry + atr_value * atr_mult
    raise ValueError(f"direction must be 'up' or 'down', got {direction!r}")


def should_activate_chandelier(
    direction: str, entry_price: float, current_price: float, stop_price: float, *, activation_r_multiple: float = 1.0
) -> bool:
    """Only start trailing once price has moved at least
    activation_r_multiple times the original risk in the favorable
    direction - same "don't trail from bar one" principle as
    trading_bot.technical_strategy.should_activate_trailing."""
    risk_dist = abs(entry_price - stop_price)
    if risk_dist == 0:
        return False
    moved = (current_price - entry_price) if direction == "up" else (entry_price - current_price)
    return moved >= risk_dist * activation_r_multiple


def risk_based_quantity(capital: float, risk_pct_per_trade: float, entry_price: float, stop_price: float, lot_size: int = 1) -> int:
    """Risk-based position sizing: risk_pct_per_trade% of `capital` is the
    max rupee loss if the stop is hit; quantity is that budget divided by
    the per-unit stop distance, rounded DOWN to whole lots (the risk
    budget is a ceiling, never rounded up past it)."""
    risk_budget = capital * (risk_pct_per_trade / 100.0)
    per_unit_risk = abs(entry_price - stop_price)
    if per_unit_risk <= 0 or lot_size <= 0:
        return 0
    lots = int((risk_budget / per_unit_risk) // lot_size)
    return max(0, lots) * lot_size


@dataclass
class PortfolioRiskState:
    starting_capital: float
    current_capital: float
    peak_capital: float
    daily_realized_pnl: float = 0.0
    consecutive_losses: int = 0


@dataclass
class RiskDecision:
    allowed: bool
    risk_multiplier: float  # scales the normal risk_pct_per_trade; 1.0 = no change, 0.0 = blocked
    reason: str


def evaluate_portfolio_risk(
    state: PortfolioRiskState,
    *,
    daily_loss_limit_pct: float = 3.0,
    max_consecutive_losses: int = 3,
    consecutive_loss_risk_multiplier: float = 0.5,
    drawdown_reduction_thresholds: tuple[tuple[float, float], ...] = ((10.0, 0.5), (20.0, 0.0)),
) -> RiskDecision:
    """Portfolio-level gate, evaluated BEFORE sizing any new trade,
    independent of that trade's own signal quality:

    - Daily loss limit: today's realized P&L already at/beyond
      -daily_loss_limit_pct% of starting capital -> no new trades today.
    - Drawdown-based risk reduction: `drawdown_reduction_thresholds` is
      (drawdown_pct, risk_multiplier) pairs; the deepest breached
      threshold wins (default: half size at a 10% drawdown from peak,
      fully blocked at 20%).
    - Consecutive-loss protection: at/beyond `max_consecutive_losses` in a
      row, new trades get sized down by consecutive_loss_risk_multiplier
      (stacks multiplicatively with a drawdown reduction, never overrides
      a drawdown BLOCK) rather than being shut off outright - a strategy
      in a rough patch isn't necessarily broken.
    """
    daily_loss_pct = (-state.daily_realized_pnl / state.starting_capital * 100.0) if state.starting_capital else 0.0
    if daily_loss_pct >= daily_loss_limit_pct:
        return RiskDecision(False, 0.0, "daily_loss_limit_breached")

    drawdown_pct = ((state.peak_capital - state.current_capital) / state.peak_capital * 100.0) if state.peak_capital else 0.0
    dd_multiplier = 1.0
    dd_reason = None
    for threshold_pct, mult in sorted(drawdown_reduction_thresholds, key=lambda t: t[0]):
        if drawdown_pct >= threshold_pct:
            dd_multiplier = mult
            dd_reason = f"drawdown_{threshold_pct:g}pct_reduction"
    if dd_multiplier <= 0.0:
        return RiskDecision(False, 0.0, dd_reason or "drawdown_block")

    if state.consecutive_losses >= max_consecutive_losses:
        multiplier = min(dd_multiplier, consecutive_loss_risk_multiplier)
        reason = f"{dd_reason}+consecutive_loss_reduction" if dd_reason else "consecutive_loss_reduction"
    else:
        multiplier = dd_multiplier
        reason = dd_reason or "normal"

    return RiskDecision(True, multiplier, reason)


DEFAULT_CORRELATION_GROUPS = {"NIFTY": "index", "BANKNIFTY": "index", "SENSEX": "index"}


def correlated_exposure_multiplier(
    candidate_underlying: str,
    candidate_direction: str,
    open_positions: list[tuple[str, str]],
    correlation_groups: dict[str, str] = None,
) -> float:
    """Reduces size when a new trade would stack same-direction exposure on
    an already-open, correlated underlying (NIFTY/BANKNIFTY/SENSEX move
    together on days that matter - same three underlyings
    trading_bot/run_technical.py already watches). Returns a risk
    multiplier: 1.0 unaffected, 0.5 for one existing same-direction
    correlated position, 0.0 (blocked) for two or more."""
    groups = correlation_groups if correlation_groups is not None else DEFAULT_CORRELATION_GROUPS
    group = groups.get(candidate_underlying)
    if group is None:
        return 1.0
    same_group_same_direction = sum(
        1
        for underlying, direction in open_positions
        if underlying != candidate_underlying and groups.get(underlying) == group and direction == candidate_direction
    )
    if same_group_same_direction <= 0:
        return 1.0
    if same_group_same_direction == 1:
        return 0.5
    return 0.0
