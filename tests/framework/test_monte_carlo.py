from research.framework.backtest_engine import ClosedTrade
from research.framework.monte_carlo import run_monte_carlo


def _trade(net_pnl):
    return ClosedTrade(
        underlying="TEST", strategy="trend_following", direction="up", entry_date=None, exit_date=None,
        entry_price=100.0, exit_price=100.0 + net_pnl, quantity=1, gross_pnl=net_pnl, costs=0.0,
        net_pnl=net_pnl, exit_reason="target", r_multiple=1.0, entry_regime_trend="up", entry_regime_volatility="normal",
    )


def test_run_monte_carlo_empty_trades():
    result = run_monte_carlo([], 100000.0)
    assert result.n_simulations == 0
    assert result.probability_of_ruin == 0.0


def test_run_monte_carlo_deterministic_with_seed():
    trades = [_trade(100.0), _trade(-50.0), _trade(200.0), _trade(-300.0)]
    r1 = run_monte_carlo(trades, 10000.0, n_simulations=500, seed=42)
    r2 = run_monte_carlo(trades, 10000.0, n_simulations=500, seed=42)
    assert r1.median_final_pnl == r2.median_final_pnl
    assert r1.probability_of_ruin == r2.probability_of_ruin


def test_run_monte_carlo_all_wins_has_zero_ruin_probability():
    trades = [_trade(100.0), _trade(150.0), _trade(80.0)]
    result = run_monte_carlo(trades, 100000.0, n_simulations=300, seed=1)
    assert result.probability_of_ruin == 0.0
    assert result.median_final_pnl > 0


def test_run_monte_carlo_large_losses_can_trigger_ruin():
    trades = [_trade(-9000.0), _trade(-9000.0), _trade(100.0)]
    result = run_monte_carlo(trades, 10000.0, n_simulations=500, ruin_threshold_pct=50.0, seed=1)
    assert result.probability_of_ruin > 0.0
