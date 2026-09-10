import datetime as dt

from research.framework.backtest_engine import BacktestConfig
from research.framework.walk_forward import ParameterGrid, make_folds, run_walk_forward


def _candle(date, o, h, l, c, v=1000):
    return {"date": date, "open": o, "high": h, "low": l, "close": c, "volume": v}


def _trending_candles(n):
    d0 = dt.date(2020, 1, 1)
    candles = []
    price = 100.0
    for i in range(n):
        noise = 1.5 if i % 3 == 0 else (-1.0 if i % 3 == 1 else 0.5)
        price += 0.8
        base = price + noise
        candles.append(_candle(d0 + dt.timedelta(days=i), base, base + 2, base - 2, base, 1000))
    return candles


def test_make_folds_basic_shape():
    folds = make_folds(n_candles=300, train_size=60, validate_size=20, test_size=20)
    assert len(folds) >= 1
    for f in folds:
        assert f.train_end - f.train_start == 60
        assert f.validate_end - f.train_end == 20
        assert f.test_end - f.validate_end == 20
        assert f.test_end <= 300


def test_make_folds_rejects_non_positive_sizes():
    try:
        make_folds(100, 0, 10, 10)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_parameter_grid_combinations_count():
    grid = ParameterGrid(min_score=(60.0, 70.0), stop_atr_mult=(1.5,), reward_risk_ratio=(2.0, 3.0))
    combos = grid.combinations()
    assert len(combos) == 4
    assert all(set(c.keys()) == {"min_score", "stop_atr_mult", "reward_risk_ratio"} for c in combos)


def test_run_walk_forward_produces_fold_results_with_out_of_sample_trades():
    candles = _trending_candles(220)
    grid = ParameterGrid(min_score=(55.0, 65.0), stop_atr_mult=(1.5,), reward_risk_ratio=(2.0,))
    base_config = BacktestConfig(lot_size=1, starting_capital=100000.0)
    result = run_walk_forward(
        candles, "TEST", "equity_delivery", "trend_following", base_config, grid,
        train_size=60, validate_size=20, test_size=20,
    )
    assert len(result.folds) >= 1
    for fr in result.folds:
        assert set(fr.chosen_params.keys()) == {"min_score", "stop_atr_mult", "reward_risk_ratio"}
        assert fr.test_metrics.trade_count == len(fr.test_trades)
    agg = result.aggregate_test_metrics(base_config.starting_capital)
    assert agg.trade_count == sum(len(fr.test_trades) for fr in result.folds)
