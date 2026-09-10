import datetime as dt

from research.framework.backtest_engine import BacktestConfig
from research.framework.sensitivity import run_sensitivity


def _candle(date, o, h, l, c, v=1000):
    return {"date": date, "open": o, "high": h, "low": l, "close": c, "volume": v}


def _trending_candles(n):
    d0 = dt.date(2020, 1, 1)
    candles = []
    price = 100.0
    for i in range(n):
        price += 0.8
        candles.append(_candle(d0 + dt.timedelta(days=i), price, price + 2, price - 1, price, 1000))
    return candles


def test_run_sensitivity_runs_without_crashing_and_reports_points():
    candles = _trending_candles(200)
    base_config = BacktestConfig(min_history_bars=60, lot_size=1, starting_capital=100000.0)
    report = run_sensitivity(
        candles, "TEST", "equity_delivery", "trend_following", base_config,
        perturb_params=("min_score", "stop_atr_mult"), perturbation_pcts=(-10.0, 10.0),
    )
    assert len(report.points) == 4  # 2 params x 2 perturbation pcts
    assert all(p.parameter in ("min_score", "stop_atr_mult") for p in report.points)
    assert isinstance(report.fragile_parameters, list)


def test_run_sensitivity_zero_baseline_pnl_marks_nothing_fragile():
    # A completely flat market: baseline trades = 0, baseline_net_pnl = 0 -
    # nothing to compare against, so no perturbation can be flagged fragile.
    d0 = dt.date(2020, 1, 1)
    flat = [_candle(d0 + dt.timedelta(days=i), 100.0, 100.5, 99.5, 100.0) for i in range(150)]
    base_config = BacktestConfig(min_history_bars=60, lot_size=1, starting_capital=100000.0)
    report = run_sensitivity(
        flat, "TEST", "equity_delivery", "trend_following", base_config,
        perturb_params=("min_score",), perturbation_pcts=(-10.0, 10.0),
    )
    assert report.baseline_net_pnl == 0.0
    assert report.fragile_parameters == []
