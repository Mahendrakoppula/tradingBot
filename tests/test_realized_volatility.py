import math

import pandas as pd
import pytest

from features.realized_volatility import realized_volatility, realized_volatility_as_of

# Deterministic alternating +1%/-1% log returns - closed-form expected
# stdev, no reliance on random-seed "looks about right" assertions.
_R = 0.01
_CLOSES = pd.Series([100.0, 100.0 * math.exp(_R), 100.0, 100.0 * math.exp(_R), 100.0])


def test_realized_volatility_matches_closed_form_for_alternating_returns():
    result = realized_volatility(_CLOSES, window=4, bars_per_year=252)
    expected_stdev = _R * math.sqrt(4 / 3)  # sample stdev (ddof=1) of [r,-r,r,-r]
    expected_annualized = expected_stdev * math.sqrt(252)
    assert result.iloc[-1] == pytest.approx(expected_annualized, rel=1e-9)


def test_insufficient_history_gives_nan_not_an_error():
    result = realized_volatility(_CLOSES, window=10, bars_per_year=252)
    assert result.isna().all()


def test_as_of_uses_only_history_up_to_that_index():
    long_closes = pd.concat([_CLOSES] * 4, ignore_index=True)  # 20 bars
    as_of_5 = realized_volatility_as_of(long_closes, as_of_index=5, window=4)

    mutated = long_closes.copy()
    mutated.iloc[6:] = 999999.0  # blow up every bar AFTER as_of_index
    as_of_5_after_future_mutation = realized_volatility_as_of(mutated, as_of_index=5, window=4)

    assert as_of_5 == as_of_5_after_future_mutation


def test_as_of_returns_none_when_not_enough_history_yet():
    assert realized_volatility_as_of(_CLOSES, as_of_index=2, window=10) is None


def test_as_of_raises_for_out_of_range_index():
    with pytest.raises(IndexError):
        realized_volatility_as_of(_CLOSES, as_of_index=100, window=4)


def test_as_of_matches_full_series_value_at_that_position():
    long_closes = pd.concat([_CLOSES] * 4, ignore_index=True)
    full = realized_volatility(long_closes, window=4, bars_per_year=252)
    as_of = realized_volatility_as_of(long_closes, as_of_index=10, window=4)
    assert as_of == pytest.approx(full.iloc[10])
