"""requires_real_data tests need data/raw/NIFTY/ONE_DAY.parquet (see
tests/test_feature_engineering.py's module docstring for why)."""
import pandas as pd
import pytest

from backtesting.event_loop import BacktestConfig, _check_stop_target_hit, run_backtest
from backtesting.trade_record import Trade
from data.storage import load_ohlcv

NIFTY_DAILY = load_ohlcv("NIFTY", "ONE_DAY")
requires_real_data = pytest.mark.skipif(len(NIFTY_DAILY) == 0, reason="real NIFTY daily data not pulled locally")


def _ce_trade(stop=24000.0, target=25000.0) -> Trade:
    return Trade("t", "CE", 0, None, 24500.0, 100.0, 24500.0, None, stop_price=stop, target_price=target)


def _pe_trade(stop=25000.0, target=24000.0) -> Trade:
    return Trade("t", "PE", 0, None, 24500.0, 100.0, 24500.0, None, stop_price=stop, target_price=target)


def _bar(high, low) -> pd.Series:
    return pd.Series({"high": high, "low": low})


def test_ce_stop_hit_when_low_breaches_stop():
    assert _check_stop_target_hit(_ce_trade(), _bar(high=24600, low=23900)) == "stop"


def test_ce_target_hit_when_high_breaches_target():
    assert _check_stop_target_hit(_ce_trade(), _bar(high=25100, low=24400)) == "target"


def test_ce_neither_hit_stays_none():
    assert _check_stop_target_hit(_ce_trade(), _bar(high=24700, low=24300)) is None


def test_ce_prefers_stop_when_both_breached_same_bar():
    assert _check_stop_target_hit(_ce_trade(), _bar(high=25100, low=23900)) == "stop"


def test_pe_stop_hit_when_high_breaches_stop():
    assert _check_stop_target_hit(_pe_trade(), _bar(high=25100, low=24400)) == "stop"


def test_pe_target_hit_when_low_breaches_target():
    assert _check_stop_target_hit(_pe_trade(), _bar(high=24700, low=23900)) == "target"


def test_pe_prefers_stop_when_both_breached_same_bar():
    assert _check_stop_target_hit(_pe_trade(), _bar(high=25100, low=23900)) == "stop"


@requires_real_data
def test_run_backtest_end_to_end_produces_only_closed_trades():
    result = run_backtest(NIFTY_DAILY, BacktestConfig(warmup_bars=30))
    assert len(result.trades) > 0
    for trade in result.trades:
        assert not trade.is_open
        assert trade.pnl is not None
        assert trade.exit_reason in ("stop", "target", "end_of_data")
        assert trade.exit_index > trade.entry_index


@requires_real_data
def test_backtest_is_leakage_free_prefix_matches_full_run():
    """The core automated leakage test: running on a PREFIX of the data
    must produce entry decisions identical to the full run, at every
    bar the prefix actually reached - proving a decision at bar t never
    depends on data at or after t+1."""
    cutoff = 300
    config = BacktestConfig(warmup_bars=30)

    truncated = run_backtest(NIFTY_DAILY.iloc[:cutoff], config)
    full = run_backtest(NIFTY_DAILY, config)

    assert len(truncated.trades) > 0, "test cutoff too small to produce any trades - not a meaningful check"

    for truncated_trade in truncated.trades:
        matches = [t for t in full.trades if t.entry_index == truncated_trade.entry_index]
        assert len(matches) == 1, f"expected exactly one matching full-run trade at entry_index={truncated_trade.entry_index}"
        full_trade = matches[0]
        assert full_trade.direction == truncated_trade.direction
        assert full_trade.strategy_name == truncated_trade.strategy_name
        assert full_trade.strike == truncated_trade.strike
        assert full_trade.entry_premium == pytest.approx(truncated_trade.entry_premium)
        assert full_trade.stop_price == pytest.approx(truncated_trade.stop_price)
        assert full_trade.target_price == pytest.approx(truncated_trade.target_price)
