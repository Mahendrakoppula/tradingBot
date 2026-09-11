import pytest

from dashboard.backtest_data import run_backtest_for_dashboard
from data.storage import load_ohlcv

NIFTY_DAILY = load_ohlcv("NIFTY", "ONE_DAY")
requires_real_data = pytest.mark.skipif(len(NIFTY_DAILY) == 0, reason="real NIFTY daily data not pulled locally")


@requires_real_data
def test_run_backtest_for_dashboard_returns_trades_and_summary():
    result = run_backtest_for_dashboard("NIFTY")
    assert result is not None
    assert result.summary.n_trades == len(result.trades)
    assert result.summary.n_trades > 0


def test_run_backtest_for_dashboard_returns_none_for_unpulled_instrument():
    result = run_backtest_for_dashboard("NOT_A_REAL_INSTRUMENT")
    assert result is None


@requires_real_data
def test_contract_selector_flag_is_passed_through():
    without = run_backtest_for_dashboard("NIFTY", use_contract_selector=False)
    with_selector = run_backtest_for_dashboard("NIFTY", use_contract_selector=True)
    # same trade count (strike choice doesn't affect entry timing), but
    # different P&L confirms the flag actually took effect
    assert without.summary.n_trades == with_selector.summary.n_trades
    assert without.summary.total_pnl != with_selector.summary.total_pnl
