import numpy as np
import pandas as pd
import pytest

from risk.dynamic_stops import compute_stop_and_target, update_trailing_stop
from tests.test_market_state_structure import _zigzag_prices


def _df_from_prices(prices: list[float]) -> pd.DataFrame:
    n = len(prices)
    ts = pd.bdate_range("2026-01-01", periods=n)
    return pd.DataFrame({
        "timestamp": ts, "open": prices, "high": [p + 0.3 for p in prices], "low": [p - 0.3 for p in prices],
        "close": prices, "volume": [0] * n,
    })


def _random_walk_df(n: int = 60, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = 24500 + np.cumsum(rng.normal(0, 20, n))
    ts = pd.bdate_range("2026-01-01", periods=n)
    return pd.DataFrame({"timestamp": ts, "open": closes, "high": closes + 15, "low": closes - 15,
                          "close": closes, "volume": [0] * n})


def test_invalid_direction_raises():
    df = _random_walk_df()
    with pytest.raises(ValueError):
        compute_stop_and_target(df, entry_index=30, direction="XX")


def test_insufficient_history_for_atr_raises():
    df = _random_walk_df(n=5)
    with pytest.raises(ValueError):
        compute_stop_and_target(df, entry_index=3, direction="CE")


def test_ce_stop_is_below_entry_and_target_is_above():
    df = _random_walk_df()
    levels = compute_stop_and_target(df, entry_index=40, direction="CE")
    assert levels.stop_price < levels.entry_price < levels.target_price
    assert levels.stop_distance_points > 0
    assert levels.target_distance_points > 0


def test_pe_stop_is_above_entry_and_target_is_below():
    df = _random_walk_df()
    levels = compute_stop_and_target(df, entry_index=40, direction="PE")
    assert levels.target_price < levels.entry_price < levels.stop_price


def test_falls_back_to_atr_when_no_structure_level_exists():
    # Monotonically rising prices - a CE entry has no swing LOW below it
    # within the visible history, so it must fall back to pure ATR.
    prices = [100 + 0.5 * i for i in range(60)]
    df = _df_from_prices(prices)
    levels = compute_stop_and_target(df, entry_index=55, direction="CE")
    assert levels.stop_source == "atr"


def test_uses_structure_when_it_is_wider_than_atr():
    # entry_index=13 sits right after a deep, well-confirmed swing low
    # (index 9, price ~24000) with no closer support yet confirmed - its
    # distance from entry (~684 points) is far wider than a 5-period ATR
    # off these same volatile-zigzag bars (verified: ATR-based distance
    # comes out under 300), so structure should win on width alone with
    # realistic (not artificially tiny) multipliers.
    zigzag = _zigzag_prices([24500, 24600, 24000, 24700, 24650, 24750])
    df = _df_from_prices(zigzag)
    levels = compute_stop_and_target(df, entry_index=13, direction="CE", atr_period=5)
    assert levels.stop_source == "structure"
    assert levels.stop_price == pytest.approx(23999.7)


def test_stop_never_negative_or_absurd_relative_to_entry():
    df = _random_walk_df()
    levels = compute_stop_and_target(df, entry_index=40, direction="CE")
    assert levels.stop_price > 0
    assert levels.stop_distance_points < levels.entry_price  # sanity bound, not a tight assertion


# --- trailing stop ---

def test_ce_trailing_stop_moves_up_as_price_rises():
    stop = 24400.0
    stop = update_trailing_stop("CE", stop, current_price=24600, atr_value=50, trail_atr_multiplier=2.0)
    assert stop == 24500.0  # 24600 - 2*50


def test_ce_trailing_stop_never_loosens_on_a_pullback():
    stop = update_trailing_stop("CE", 24500.0, current_price=24600, atr_value=50, trail_atr_multiplier=2.0)
    # price pulls back - candidate (24450 - 100 = 24350) is worse than current stop, must not loosen
    stop_after_pullback = update_trailing_stop("CE", stop, current_price=24450, atr_value=50, trail_atr_multiplier=2.0)
    assert stop_after_pullback == stop


def test_pe_trailing_stop_moves_down_as_price_falls():
    stop = 24700.0
    stop = update_trailing_stop("PE", stop, current_price=24500, atr_value=50, trail_atr_multiplier=2.0)
    assert stop == 24600.0  # 24500 + 2*50


def test_pe_trailing_stop_never_loosens_on_a_bounce():
    stop = update_trailing_stop("PE", 24600.0, current_price=24500, atr_value=50, trail_atr_multiplier=2.0)
    stop_after_bounce = update_trailing_stop("PE", stop, current_price=24550, atr_value=50, trail_atr_multiplier=2.0)
    assert stop_after_bounce == stop


def test_trailing_stop_invalid_direction_raises():
    with pytest.raises(ValueError):
        update_trailing_stop("XX", 100.0, 105.0, atr_value=5.0)
