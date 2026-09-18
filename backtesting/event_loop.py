"""Event-driven backtester (spec Phase 12). Processes bars strictly in
order, one at a time; every decision at bar t uses ONLY
history = df.iloc[:t+1] - data at or after t+1 is never touched by any
decision made at t. tests/test_event_loop.py's leakage test verifies
this by running the engine on a truncated prefix of the data and
asserting every entry decision made within that prefix is byte-for-byte
identical to the same decision made by the full-data run - an actual
automated check, not just a docstring claim.

Deliberately scoped for this first pass - real, honest simplifications,
not hidden ones:

  - Originally ONE_DAY bars only, where "a new trading day" was trivial
    (every bar IS one day). process_bar() now takes an explicit
    `is_new_day` flag (default True, preserving that exact original
    behavior for existing callers) so run_backtest() can also drive it
    off REAL calendar-day boundaries computed from each bar's own
    timestamp - this is what makes intraday bars (5-minute, hourly,
    etc.) safe to feed in at all. Before this, calling process_bar()
    once per 5-minute bar would have reset the daily-loss kill switch
    every 5 minutes instead of once per real day, silently defeating it -
    a real bug caught while building this intraday support, not a
    hypothetical one (see backtesting/BACKTESTS.md). Warmup windows,
    ATR periods, and every strategy's own lookback parameters were only
    ever tuned/validated against DAILY-bar semantics though - running
    intraday with the same numeric parameters is an honest first look,
    not a separately validated intraday configuration.
  - P&L is in OPTION PREMIUM terms via the spot-proxy theoretical
    Black-Scholes engine (features/theoretical_options.py), ONE
    conceptual unit - not lot-size-multiplied. Real position sizing
    (risk/position_sizing.py) is kept separate on purpose: conflating it
    here would make it impossible to tell whether a strategy's raw edge
    is real, versus lot-size choices doing the work.
  - Strike = spot rounded to the nearest `strike_increment` at entry
    (ATM). Expiry = entry date + a fixed `days_to_expiry` - no real
    weekly-expiry trading calendar exists yet (a separate, later phase).
  - If both the stop and target are breached within the same bar (a
    wide-range day), the STOP is assumed to have been hit first - the
    conservative assumption, not an attempt to guess which happened
    first intrabar. VALIDATED (backtesting/tie_break_validation.py,
    BACKTESTS.md's Investigation 003): across the full 5-year history,
    on all three indices, this assumption was never actually invoked -
    zero trades ever closed on a bar breaching both stop and target
    simultaneously, given risk/dynamic_stops.py's current ATR-based
    widths. Every result in this log is unaffected by this assumption,
    not because it was proven correct, but because it never mattered.
  - Only one position open at a time.
"""
import datetime as dt
from dataclasses import dataclass, field

import pandas as pd

from backtesting.trade_record import Trade
from features.theoretical_options import theoretical_option_snapshot
from market_state.classifier import classify_market_state
from market_state.volatility import atr as compute_atr
from risk.daily_risk_engine import DailyRiskEngine
from risk.dynamic_stops import compute_stop_and_target, update_trailing_stop
from strategies.contract_selection import select_contract
from strategies.portfolio import generate_candidate_signals
from strategies.ranking import select_best_signal

DEFAULT_WARMUP_BARS = 30


@dataclass
class BacktestConfig:
    risk_free_rate: float = 0.07
    strike_increment: float = 50.0
    days_to_expiry: int = 7
    stop_atr_multiplier: float | None = None  # None = risk.dynamic_stops' own default
    target_atr_multiplier: float | None = None
    trail_atr_multiplier: float | None = None
    max_daily_loss: float = 2000.0
    profit_protection_level: float = 800.0
    profit_selectivity_level: float = 1000.0
    warmup_bars: int = DEFAULT_WARMUP_BARS
    strategies: list | None = None  # None = strategies.portfolio.DEFAULT_STRATEGIES
    # Default False preserves the exact ATM-only behavior every number in
    # backtesting/BACKTESTS.md was produced with - flip to True only as a
    # deliberate, separately-logged A/B experiment (spec Phase 9's
    # contract selector), never as a silent default change that would
    # invalidate already-logged results without a new entry explaining why.
    use_contract_selector: bool = False
    contract_selector_offsets: tuple[int, ...] = (-2, -1, 0, 1, 2)


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)


def _check_stop_target_hit(trade: Trade, bar: pd.Series) -> str | None:
    """Returns "stop", "target", or None. STOP checked first if both
    are breached in the same bar - see module docstring."""
    if trade.direction == "CE":
        if bar["low"] <= trade.stop_price:
            return "stop"
        if bar["high"] >= trade.target_price:
            return "target"
    else:
        if bar["high"] >= trade.stop_price:
            return "stop"
        if bar["low"] <= trade.target_price:
            return "target"
    return None


def _close_trade(trade: Trade, df: pd.DataFrame, exit_index: int, reason: str, risk_free_rate: float) -> None:
    trade.exit_index = exit_index
    trade.exit_timestamp = df["timestamp"].iloc[exit_index]
    trade.exit_spot = float(df["close"].iloc[exit_index])
    exit_snapshot = theoretical_option_snapshot(
        df, exit_index, trade.strike, trade.expiry, trade.direction, risk_free_rate,
    )
    trade.exit_premium = exit_snapshot.price if exit_snapshot is not None else 0.0
    trade.exit_reason = reason
    trade.pnl = trade.exit_premium - trade.entry_premium


def process_bar(
    df: pd.DataFrame,
    t: int,
    open_trade: Trade | None,
    daily_risk: DailyRiskEngine,
    config: BacktestConfig,
    is_last_bar: bool = False,
    is_new_day: bool = True,
) -> tuple[Trade | None, Trade | None]:
    """Processes exactly ONE bar of decision-making. Shared by
    run_backtest() below and paper_trading/'s live daily loop, so both
    use IDENTICAL decision logic rather than a hand-copied
    reimplementation that could silently drift from the validated
    backtester - the classic backtest/live logic divergence bug class
    this refactor exists to rule out structurally, not just by
    discipline.

    Returns (new_open_trade, closed_trade) - closed_trade is non-None
    only on the bar a position actually closed this call.

    `is_last_bar`: True only for the backtester's final bar (nothing
    left to ever manage a position opened there - see the last-bar edge
    case fixed in this project's history). Always False for live use,
    where there is no "last bar" until the process itself stops.

    `is_new_day`: True (default) means this call's bar starts a new
    real trading day and the daily risk engine's P&L resets here -
    correct unconditionally for daily bars (every bar IS a new day,
    the original, only-ever-used behavior) and for paper_trading/'s
    once-daily call (a fresh DailyRiskEngine is constructed each call
    there anyway, so the reset is a no-op either way). For INTRADAY
    bars, run_backtest() computes this from real calendar-day
    boundaries and passes False for every bar after the first one on a
    given day - without this, a bad early-day loss would get wiped out
    by the very next 5-minute bar instead of persisting for the rest of
    that real day, defeating the daily-loss kill switch. (A previous,
    different-direction version of this same bug - the reset never
    firing AT ALL - was caught via Monte Carlo validation; see
    backtesting/BACKTESTS.md.)
    """
    history = df.iloc[: t + 1]  # the one and only leakage boundary
    bar = df.iloc[t]

    if is_new_day:
        daily_risk.reset_day()

    stop_kwargs = {}
    if config.stop_atr_multiplier is not None:
        stop_kwargs["stop_atr_multiplier"] = config.stop_atr_multiplier
    if config.target_atr_multiplier is not None:
        stop_kwargs["target_atr_multiplier"] = config.target_atr_multiplier
    trail_multiplier_kwargs = {}
    if config.trail_atr_multiplier is not None:
        trail_multiplier_kwargs["trail_atr_multiplier"] = config.trail_atr_multiplier

    if open_trade is not None:
        reason = _check_stop_target_hit(open_trade, bar)
        if reason is not None:
            _close_trade(open_trade, history, t, reason, config.risk_free_rate)
            daily_risk.record_realized_pnl(open_trade.pnl)
            return None, open_trade
        atr_series = compute_atr(history)
        atr_value = atr_series.iloc[-1] if len(atr_series) else float("nan")
        if pd.notna(atr_value) and atr_value > 0:
            open_trade.stop_price = update_trailing_stop(
                open_trade.direction, open_trade.stop_price, float(bar["close"]),
                atr_value=float(atr_value), **trail_multiplier_kwargs,
            )
        return open_trade, None

    if is_last_bar:
        return None, None  # no bars left to ever manage a position opened here - not a real trade opportunity

    decision = daily_risk.evaluate()
    if not decision.allow_new_trades:
        return None, None

    market_state = classify_market_state(history)
    signals = generate_candidate_signals(history, market_state, mtf=None, strategies=config.strategies)
    qualified = [s for s in signals if s.confidence >= decision.min_confidence_required]
    best = select_best_signal(qualified)
    if best is None:
        return None, None

    try:
        levels = compute_stop_and_target(history, t, best.direction, **stop_kwargs)
    except ValueError:
        return None, None

    entry_price = float(bar["close"])
    entry_date = bar["timestamp"].date() if hasattr(bar["timestamp"], "date") else bar["timestamp"]
    expiry = entry_date + dt.timedelta(days=config.days_to_expiry)

    if config.use_contract_selector:
        candidate = select_contract(
            history, t, best.direction, expiry, config.risk_free_rate,
            config.strike_increment, strike_offsets=config.contract_selector_offsets,
        )
        if candidate is None:
            return None, None
        strike, entry_snapshot = candidate.strike, candidate.snapshot
    else:
        strike = round(entry_price / config.strike_increment) * config.strike_increment
        entry_snapshot = theoretical_option_snapshot(history, t, strike, expiry, best.direction, config.risk_free_rate)
        if entry_snapshot is None:
            return None, None

    new_trade = Trade(
        strategy_name=best.strategy_name, direction=best.direction, entry_index=t,
        entry_timestamp=bar["timestamp"], entry_spot=entry_price, entry_premium=entry_snapshot.price,
        strike=strike, expiry=expiry, stop_price=levels.stop_price, target_price=levels.target_price,
        entry_regime=market_state.regime,
    )
    return new_trade, None


def _bar_date(df: pd.DataFrame, t: int) -> dt.date:
    ts = df.iloc[t]["timestamp"]
    return ts.date() if hasattr(ts, "date") else ts


def run_backtest(df: pd.DataFrame, config: BacktestConfig | None = None) -> BacktestResult:
    config = config or BacktestConfig()
    n = len(df)
    trades: list[Trade] = []
    open_trade: Trade | None = None
    daily_risk = DailyRiskEngine(config.max_daily_loss, config.profit_protection_level, config.profit_selectivity_level)
    previous_date: dt.date | None = None

    for t in range(config.warmup_bars, n):
        bar_date = _bar_date(df, t)
        is_new_day = previous_date is None or bar_date != previous_date
        previous_date = bar_date
        open_trade, closed = process_bar(
            df, t, open_trade, daily_risk, config, is_last_bar=(t == n - 1), is_new_day=is_new_day,
        )
        if closed is not None:
            trades.append(closed)

    if open_trade is not None:
        _close_trade(open_trade, df, n - 1, "end_of_data", config.risk_free_rate)
        trades.append(open_trade)

    return BacktestResult(trades=trades)
