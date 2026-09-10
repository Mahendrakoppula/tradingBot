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

  - Runs on ONE_DAY bars only. This makes "a new trading day" trivial
    (every bar IS one day - no intraday session-boundary detection
    needed), at the cost of not yet testing the intraday cadence the
    live system is actually meant to run at. A true intraday event loop
    is a natural extension of this same engine, not built here.
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
    first intrabar.
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
from strategies.portfolio import generate_candidate_signals

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


def run_backtest(df: pd.DataFrame, config: BacktestConfig | None = None) -> BacktestResult:
    config = config or BacktestConfig()
    n = len(df)
    trades: list[Trade] = []
    open_trade: Trade | None = None
    daily_risk = DailyRiskEngine(config.max_daily_loss, config.profit_protection_level, config.profit_selectivity_level)

    stop_kwargs = {}
    if config.stop_atr_multiplier is not None:
        stop_kwargs["stop_atr_multiplier"] = config.stop_atr_multiplier
    if config.target_atr_multiplier is not None:
        stop_kwargs["target_atr_multiplier"] = config.target_atr_multiplier
    trail_multiplier_kwargs = {}
    if config.trail_atr_multiplier is not None:
        trail_multiplier_kwargs["trail_atr_multiplier"] = config.trail_atr_multiplier

    for t in range(config.warmup_bars, n):
        history = df.iloc[: t + 1]  # the one and only leakage boundary
        bar = df.iloc[t]

        # Each bar IS one full trading day in this daily-bars-only first
        # pass (see module docstring) - the daily risk engine's P&L must
        # reset here every bar, or a bad day early in a multi-year
        # backtest permanently freezes it for every day after (a real
        # bug this project caught via Monte Carlo validation surfacing
        # an implausibly small trade count on BANKNIFTY - see
        # backtesting/BACKTESTS.md).
        daily_risk.reset_day()

        if open_trade is not None:
            reason = _check_stop_target_hit(open_trade, bar)
            if reason is not None:
                _close_trade(open_trade, history, t, reason, config.risk_free_rate)
                daily_risk.record_realized_pnl(open_trade.pnl)
                trades.append(open_trade)
                open_trade = None
            else:
                atr_series = compute_atr(history)
                atr_value = atr_series.iloc[-1] if len(atr_series) else float("nan")
                if pd.notna(atr_value) and atr_value > 0:
                    open_trade.stop_price = update_trailing_stop(
                        open_trade.direction, open_trade.stop_price, float(bar["close"]),
                        atr_value=float(atr_value), **trail_multiplier_kwargs,
                    )
            continue

        if t == n - 1:
            continue  # no bars left to ever manage a position opened here - not a real trade opportunity

        decision = daily_risk.evaluate()
        if not decision.allow_new_trades:
            continue

        market_state = classify_market_state(history)
        signals = generate_candidate_signals(history, market_state, mtf=None, strategies=config.strategies)
        qualified = [s for s in signals if s.confidence >= decision.min_confidence_required]
        if not qualified:
            continue
        best = max(qualified, key=lambda s: s.confidence)

        try:
            levels = compute_stop_and_target(history, t, best.direction, **stop_kwargs)
        except ValueError:
            continue

        entry_price = float(bar["close"])
        strike = round(entry_price / config.strike_increment) * config.strike_increment
        entry_date = bar["timestamp"].date() if hasattr(bar["timestamp"], "date") else bar["timestamp"]
        expiry = entry_date + dt.timedelta(days=config.days_to_expiry)
        entry_snapshot = theoretical_option_snapshot(history, t, strike, expiry, best.direction, config.risk_free_rate)
        if entry_snapshot is None:
            continue

        open_trade = Trade(
            strategy_name=best.strategy_name, direction=best.direction, entry_index=t,
            entry_timestamp=bar["timestamp"], entry_spot=entry_price, entry_premium=entry_snapshot.price,
            strike=strike, expiry=expiry, stop_price=levels.stop_price, target_price=levels.target_price,
            entry_regime=market_state.regime,
        )

    if open_trade is not None:
        _close_trade(open_trade, df, n - 1, "end_of_data", config.risk_free_rate)
        trades.append(open_trade)

    return BacktestResult(trades=trades)
