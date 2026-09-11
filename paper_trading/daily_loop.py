"""Live paper-trading daily loop (spec Phase 14). Intended to run once
per trading day, near market close, via a systemd timer - NOT a
continuously running process. This matches the backtester's own
daily-bars-only scope (backtesting/event_loop.py's docstring) rather
than building untested intraday complexity on top of an engine that
hasn't been validated at that cadence either.

Reuses process_bar() - the SAME function backtesting/event_loop.py's
run_backtest() uses - so a live decision can never silently diverge
from the validated backtester's logic.

ALWAYS PAPER. This module does not import execution/order_client.py at
all - there is nothing here that COULD place a real order, structurally,
not merely because a dry_run flag happens to be set correctly.

Scheduling dependency: this must run AFTER that day's bar has been
refreshed into data/raw/ (data/pull_history.py, or a future lighter
incremental fetch) - it reads whatever is already there, it does not
fetch anything itself.
"""
import logging
from pathlib import Path

from backtesting.event_loop import BacktestConfig, process_bar
from backtesting.trade_record import Trade
from data.storage import load_ohlcv
from monitoring.notifier import notify
from paper_trading.state import STATE_DIR, PaperTradingState, load_state, save_state
from risk.daily_risk_engine import DailyRiskEngine

log = logging.getLogger("codex.paper_trading")

INSTRUMENTS = ["NIFTY", "BANKNIFTY", "SENSEX"]


def _bar_key(timestamp) -> str:
    return timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp)


def run_for_instrument(instrument: str, config: BacktestConfig | None = None, state_dir: Path = STATE_DIR) -> PaperTradingState:
    config = config or BacktestConfig()
    df = load_ohlcv(instrument, "ONE_DAY")
    if len(df) <= config.warmup_bars:
        log.warning("%s: not enough history yet (%d bars, need > %d) - skipping", instrument, len(df), config.warmup_bars)
        return load_state(instrument, state_dir)

    state = load_state(instrument, state_dir)
    t = len(df) - 1
    today_key = _bar_key(df["timestamp"].iloc[t])

    if state.last_processed_timestamp == today_key:
        log.info("%s: bar %s already processed - skipping (idempotent, safe to rerun)", instrument, today_key)
        return state

    # A fresh DailyRiskEngine each run is already correctly "reset" -
    # process_bar() calls reset_day() unconditionally on every call
    # anyway, and this runs exactly once per trading day.
    daily_risk = DailyRiskEngine(config.max_daily_loss, config.profit_protection_level, config.profit_selectivity_level)
    new_open_trade, closed_trade = process_bar(df, t, state.open_trade, daily_risk, config, is_last_bar=False)

    if closed_trade is not None:
        state.completed_trades.append(closed_trade)
        _notify_exit(instrument, closed_trade)
    elif new_open_trade is not None and state.open_trade is None:
        _notify_entry(instrument, new_open_trade)
    # else: position unchanged (still open with no exit, or no new
    # signal) - no notification, avoids daily noise for "nothing happened"

    state.open_trade = new_open_trade
    state.last_processed_timestamp = today_key
    save_state(instrument, state, state_dir)
    return state


def _notify_entry(instrument: str, trade: Trade) -> None:
    notify(
        f"\U0001F4DD <b>PAPER ENTRY</b> {instrument} {trade.direction}\n"
        f"Strategy: {trade.strategy_name} | Regime: {trade.entry_regime}\n"
        f"Strike: {trade.strike:.0f} | Premium: {trade.entry_premium:.2f}\n"
        f"Stop: {trade.stop_price:.2f} | Target: {trade.target_price:.2f}\n"
        f"(paper trade - one conceptual unit, not lot-sized)",
        html=True,
    )


def _notify_exit(instrument: str, trade: Trade) -> None:
    result = "WIN" if (trade.pnl or 0) > 0 else "LOSS"
    notify(
        f"\U0001F4C4 <b>PAPER EXIT</b> {instrument} {trade.direction} - {result}\n"
        f"Strategy: {trade.strategy_name} | Reason: {trade.exit_reason}\n"
        f"P&L: {trade.pnl:.2f} (per unit, not lot-sized)",
        html=True,
    )


def run_all(config: BacktestConfig | None = None, state_dir: Path = STATE_DIR) -> None:
    for instrument in INSTRUMENTS:
        try:
            run_for_instrument(instrument, config, state_dir)
        except Exception:
            log.exception("Paper trading run failed for %s", instrument)


if __name__ == "__main__":
    from monitoring.logging_setup import setup_logging
    setup_logging()
    run_all()
