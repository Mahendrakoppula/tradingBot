import datetime as dt

from trading_bot.engine.research.metrics import Metrics, compute, fingerprint_stats, render, segmented
from trading_bot.timeutil import IST


def _row(net, day=16, hour=10, strategy="TREND_PULLBACK", underlying="NIFTY", ot="CE", regime="BULL", fp="FP1",
         costs=60.0, exit_reason="TARGET_1", r=None, mfe=None, mae=None, hold=30.0):
    entry = dt.datetime(2026, 9, day, hour, 0, tzinfo=IST)
    return {"net_pnl": net, "gross_pnl": net + costs, "costs": costs, "exit_ts": entry + dt.timedelta(minutes=hold),
            "entry_ts": entry, "exit_reason": exit_reason, "mfe": mfe if mfe is not None else max(net, 0) + 50,
            "mae": mae if mae is not None else min(net, 0) - 20, "r_multiple": r,
            "details": {"strategy": strategy, "underlying": underlying, "option_type": ot, "regime": regime, "trend": "BULL",
                        "fingerprint": fp, "expiry": "2026-09-23", "strike": 25000.0, "holding_minutes": hold,
                        "entry_ts": entry.isoformat()}}


ROWS = [_row(300, 16, 10), _row(-200, 16, 12), _row(500, 17, 10, strategy="ORB", ot="PE", fp="FP2"), _row(-150, 17, 11),
        _row(-100, 17, 14, regime="RANGE"), _row(400, 18, 10, r=1.6), _row(250, 18, 13, r=1.0)]


def test_compute_core_metrics():
    m = compute(ROWS, capital=50000.0)
    assert m.trades == 7 and m.wins == 4 and m.losses == 3 and m.win_rate == round(4 / 7, 4)
    assert m.net_pnl == 1000.0 and m.costs == 420.0 and m.gross_pnl == 1420.0
    assert m.avg_win == 362.5 and m.avg_loss == -150.0 and m.expectancy == round(1000 / 7, 2)
    assert m.profit_factor == round(1450 / 450, 3)
    # curve: 300,100,600,450,350,750,1000 -> peak 600 trough 350 -> dd 250
    assert m.max_drawdown == 250.0 and m.max_drawdown_pct == 0.005 and m.recovery_factor == 4.0
    assert m.calmar == round((1000 / 50000) / 0.005, 3)
    assert m.max_win_streak == 2 and m.max_loss_streak == 2
    assert m.days == 3 and m.sharpe_daily is not None and m.sharpe_annualised is not None
    assert m.avg_holding_minutes == 30.0 and m.avg_r == 1.3 and m.exit_reasons == {"TARGET_1": 7}
    assert m.avg_mfe > 0 > m.avg_mae


def test_empty_and_single_day():
    assert compute([], 50000.0) == Metrics()
    m = compute([_row(100), _row(-50)], 50000.0)
    assert m.days == 1 and m.sharpe_daily is None and m.sortino_daily is None and m.profit_factor == 2.0


def test_all_wins_has_no_profit_factor_and_no_drawdown():
    m = compute([_row(100), _row(200)], 50000.0)
    assert m.profit_factor is None and m.max_drawdown == 0.0 and m.recovery_factor is None and m.calmar is None


def test_segmentation_keys():
    seg = segmented(ROWS, 50000.0)
    assert set(seg) == {"strategy", "underlying", "option_type", "trend", "regime", "time_of_day", "expiry", "strike", "fingerprint"}
    assert seg["strategy"]["ORB"].trades == 1 and seg["strategy"]["TREND_PULLBACK"].trades == 6
    assert seg["option_type"]["PE"].net_pnl == 500.0
    assert seg["regime"]["RANGE"].net_pnl == -100.0
    assert seg["time_of_day"]["10:00-11:30"].trades == 4 and seg["time_of_day"]["14:00-15:00"].trades == 1
    assert seg["strike"]["25000.0"].trades == 7


def test_fingerprint_stats_flag_reliability():
    st = fingerprint_stats(ROWS, 50000.0, min_samples=5)
    assert [s.fingerprint for s in st] == ["FP1", "FP2"]
    assert st[0].sample_size == 6 and st[0].reliable and not st[1].reliable
    assert st[0].expectancy == round(500 / 6, 2)


def test_render_is_compact():
    text = render(compute(ROWS, 50000.0), "ALL: ")
    assert text.startswith("ALL: trades=7") and "PF=" in text and "exits:" in text
