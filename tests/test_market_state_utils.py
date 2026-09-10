import numpy as np
import pandas as pd

from market_state.utils import percentile_rank_of_last, rolling_percentile_rank


def test_rolling_percentile_rank_matches_percentile_rank_of_last_at_every_row():
    rng = np.random.default_rng(42)
    series = pd.Series(rng.normal(0, 1, 200))
    lookback = 20

    rolling = rolling_percentile_rank(series, lookback)

    for t in [0, 1, 5, 19, 20, 21, 50, 100, 199]:
        window_start = max(0, t - lookback + 1)
        expected = percentile_rank_of_last(series.iloc[window_start:t + 1])
        assert rolling.iloc[t] == expected, f"mismatch at row {t}"
