import math

import pytest

from research.framework.backtest_engine import ClosedTrade
from research.framework.metrics import breakdown_by_direction, breakdown_by_regime, breakdown_by_strategy, compute_metrics


def _trade(net_pnl, r_multiple=1.0, direction="up", strategy="trend_following", regime_trend="up", regime_vol="normal"):
    return ClosedTrade(
        underlying="NIFTY", strategy=strategy, direction=direction,
        entry_date=None, exit_date=None, entry_price=100.0, exit_price=100.0 + net_pnl,
        quantity=1, gross_pnl=net_pnl, costs=0.0, net_pnl=net_pnl, exit_reason="target",
        r_multiple=r_multiple, entry_regime_trend=regime_trend, entry_regime_volatility=regime_vol,
    )


def test_compute_metrics_empty_trade_list():
    m = compute_metrics([], 100000.0)
    assert m.trade_count == 0
    assert m.win_rate is None
    assert m.total_net_pnl == 0.0


def test_compute_metrics_all_wins_profit_factor_is_infinite():
    trades = [_trade(100.0), _trade(200.0)]
    m = compute_metrics(trades, 100000.0)
    assert m.trade_count == 2
    assert m.win_rate == 1.0
    assert math.isinf(m.profit_factor)
    assert m.expectancy == 150.0
    assert m.total_net_pnl == 300.0


def test_compute_metrics_mixed_wins_and_losses():
    trades = [_trade(200.0, r_multiple=2.0), _trade(-100.0, r_multiple=-1.0)]
    m = compute_metrics(trades, 100000.0)
    assert m.win_rate == 0.5
    assert m.profit_factor == 2.0  # 200 gross profit / 100 gross loss
    assert m.avg_r_multiple == 0.5
    assert m.total_net_pnl == 100.0


def test_compute_metrics_max_drawdown_tracks_peak_to_trough():
    # +100 -> peak 100; -150 -> trough -50, drawdown from peak = 150; +50 -> -0 net
    trades = [_trade(100.0), _trade(-150.0), _trade(50.0)]
    m = compute_metrics(trades, 1000.0)
    assert m.max_drawdown_pct == pytest.approx(15.0)


def test_breakdown_by_direction_splits_correctly():
    trades = [_trade(100.0, direction="up"), _trade(-50.0, direction="up"), _trade(80.0, direction="down")]
    groups = breakdown_by_direction(trades, 100000.0)
    assert set(groups.keys()) == {"up", "down"}
    assert groups["up"].trade_count == 2
    assert groups["down"].trade_count == 1


def test_breakdown_by_regime_groups_by_trend_and_volatility_pair():
    trades = [
        _trade(100.0, regime_trend="up", regime_vol="normal"),
        _trade(-50.0, regime_trend="up", regime_vol="normal"),
        _trade(80.0, regime_trend="down", regime_vol="high"),
    ]
    groups = breakdown_by_regime(trades, 100000.0)
    assert groups[("up", "normal")].trade_count == 2
    assert groups[("down", "high")].trade_count == 1


def test_breakdown_by_strategy_groups_correctly():
    trades = [_trade(100.0, strategy="trend_following"), _trade(-20.0, strategy="breakout")]
    groups = breakdown_by_strategy(trades, 100000.0)
    assert groups["trend_following"].trade_count == 1
    assert groups["breakout"].trade_count == 1
