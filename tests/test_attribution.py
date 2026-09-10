from backtesting.attribution import breakdown_by_regime, breakdown_by_strategy
from backtesting.trade_record import Trade


def _trade(strategy: str, regime: str, pnl: float) -> Trade:
    return Trade(
        strategy, "CE", 0, None, 100.0, 5.0, 100.0, None,
        stop_price=95, target_price=110, entry_regime=regime, pnl=pnl,
    )


def test_trade_entry_regime_defaults_to_unknown():
    t = Trade("t", "CE", 0, None, 100.0, 5.0, 100.0, None, stop_price=95, target_price=110)
    assert t.entry_regime == "UNKNOWN"


def test_breakdown_by_strategy_groups_correctly():
    trades = [
        _trade("trend_following", "TRENDING_UP", 10),
        _trade("trend_following", "TRENDING_DOWN", -5),
        _trade("mean_reversion", "RANGING", 20),
    ]
    result = breakdown_by_strategy(trades)
    assert set(result.keys()) == {"trend_following", "mean_reversion"}
    assert result["trend_following"].n_trades == 2
    assert result["trend_following"].total_pnl == 5.0
    assert result["mean_reversion"].n_trades == 1
    assert result["mean_reversion"].total_pnl == 20.0


def test_breakdown_by_regime_groups_correctly():
    trades = [
        _trade("trend_following", "TRENDING_UP", 10),
        _trade("mean_reversion", "TRENDING_UP", -3),
        _trade("mean_reversion", "RANGING", 20),
    ]
    result = breakdown_by_regime(trades)
    assert set(result.keys()) == {"TRENDING_UP", "RANGING"}
    assert result["TRENDING_UP"].n_trades == 2
    assert result["TRENDING_UP"].total_pnl == 7.0
    assert result["RANGING"].n_trades == 1


def test_breakdown_of_empty_trades_returns_empty_dict():
    assert breakdown_by_strategy([]) == {}
    assert breakdown_by_regime([]) == {}
