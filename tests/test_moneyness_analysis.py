import datetime as dt

import pandas as pd
import pytest

from backtesting.equity_simulation import SimulatedTrade
from backtesting.moneyness_analysis import _otm_signed_distance_pct, analyze_moneyness_path, summarize_by_exit_reason
from backtesting.trade_record import Trade


def _df(highs: list[float], lows: list[float]) -> pd.DataFrame:
    n = len(highs)
    ts = pd.bdate_range("2026-01-01", periods=n)
    closes = [(h + l) / 2 for h, l in zip(highs, lows)]
    return pd.DataFrame({"timestamp": ts, "open": closes, "high": highs, "low": lows, "close": closes, "volume": [0] * n})


def _sim_trade(direction: str, entry_index: int, exit_index: int, entry_spot: float, exit_spot: float,
                strike: float, exit_reason: str = "target") -> SimulatedTrade:
    trade = Trade(
        strategy_name="t", direction=direction, entry_index=entry_index, entry_timestamp=dt.datetime(2026, 1, 1),
        entry_spot=entry_spot, entry_premium=5.0, strike=100.0, expiry=dt.date(2026, 1, 8),
        stop_price=95.0, target_price=110.0, exit_index=exit_index, exit_timestamp=dt.datetime(2026, 1, 3),
        exit_spot=exit_spot, exit_premium=10.0, exit_reason=exit_reason, pnl=5.0,
    )
    return SimulatedTrade(
        trade=trade, lots=1, quantity=65, capital_before=50_000, capital_at_risk=500, equity_tier="NORMAL",
        gross_pnl=100.0, cost=10.0, net_pnl=90.0, capital_after=50_090, strike=strike, entry_premium=5.0, exit_premium=10.0,
    )


def test_ce_otm_distance_sign_positive_when_below_strike():
    assert _otm_signed_distance_pct("CE", spot=95.0, strike=100.0) == pytest.approx(5.0)


def test_ce_otm_distance_sign_negative_when_above_strike():
    assert _otm_signed_distance_pct("CE", spot=105.0, strike=100.0) == pytest.approx(-5.0)


def test_pe_otm_distance_sign_positive_when_above_strike():
    assert _otm_signed_distance_pct("PE", spot=105.0, strike=100.0) == pytest.approx(5.0)


def test_pe_otm_distance_sign_negative_when_below_strike():
    assert _otm_signed_distance_pct("PE", spot=95.0, strike=100.0) == pytest.approx(-5.0)


def test_ce_never_crosses_strike_when_high_stays_below():
    df = _df(highs=[98, 99, 97], lows=[95, 96, 94])
    st = _sim_trade("CE", entry_index=0, exit_index=2, entry_spot=95, exit_spot=97, strike=100)
    path = analyze_moneyness_path(st, df)
    assert path.crossed_strike is False
    assert path.closest_moneyness_pct > 0  # still OTM at closest approach


def test_ce_crosses_strike_when_high_reaches_it_mid_hold():
    df = _df(highs=[98, 101, 99], lows=[95, 96, 94])  # bar 1's high (101) crosses strike 100
    st = _sim_trade("CE", entry_index=0, exit_index=2, entry_spot=95, exit_spot=99, strike=100)
    path = analyze_moneyness_path(st, df)
    assert path.crossed_strike is True
    assert path.closest_moneyness_pct <= 0


def test_ce_crossing_detected_even_if_exit_snapshot_alone_would_miss_it():
    """The whole point of this module: a trade whose EXIT spot never got
    near the strike can still have crossed it mid-hold (and vice versa) -
    only checking entry/exit snapshots would miss this."""
    df = _df(highs=[98, 150, 99], lows=[95, 96, 94])  # huge spike on bar 1, back down by exit
    st = _sim_trade("CE", entry_index=0, exit_index=2, entry_spot=95, exit_spot=99, strike=100)
    path = analyze_moneyness_path(st, df)
    assert path.crossed_strike is True
    assert path.exit_moneyness_pct > 0  # exit snapshot alone looks OTM


def test_pe_crosses_strike_when_low_reaches_it_mid_hold():
    df = _df(highs=[106, 105, 107], lows=[103, 99, 104])  # bar 1's low (99) crosses strike 100
    st = _sim_trade("PE", entry_index=0, exit_index=2, entry_spot=105, exit_spot=104, strike=100)
    path = analyze_moneyness_path(st, df)
    assert path.crossed_strike is True


def test_summarize_by_exit_reason_groups_correctly():
    df = _df(highs=[98, 99, 97, 98, 99, 97], lows=[95, 96, 94, 95, 96, 94])
    trades = [
        _sim_trade("CE", 0, 2, 95, 97, strike=100, exit_reason="stop"),  # never crosses (highs max 99)
        _sim_trade("CE", 3, 5, 95, 97, strike=90, exit_reason="target"),  # always past strike (highs > 90)
    ]
    summary = summarize_by_exit_reason([analyze_moneyness_path(t, df) for t in trades])
    assert summary["stop"].n_trades == 1
    assert summary["stop"].n_crossed_strike == 0
    assert summary["target"].n_trades == 1
    assert summary["target"].n_crossed_strike == 1
    assert summary["target"].fraction_crossed_strike == 1.0


def test_summarize_by_exit_reason_empty_list_returns_empty_dict():
    assert summarize_by_exit_reason([]) == {}
