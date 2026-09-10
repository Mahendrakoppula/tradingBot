"""Opening Range Breakout - a time-of-day strategy, not purely a
regime-gated one: the "opening range" (first `opening_range_bars` bars
of the CURRENT trading day, i.e. from NSE/BSE's 09:15 IST open) must be
computed fresh each day from `df`'s own timestamps, never assumed to be
the first bars of the whole multi-day history `df` may contain.
"""
import pandas as pd

from strategies.base import Signal, Strategy

DEFAULT_OPENING_RANGE_BARS = 15
# ORB doesn't have a directional prior the way trend/mean-reversion do -
# eligible everywhere except a regime we couldn't even classify.
ALL_KNOWN_REGIMES = frozenset({"TRENDING_UP", "TRENDING_DOWN", "RANGING", "VOLATILE"})


def _date_of(ts) -> object:
    return ts.date() if hasattr(ts, "date") else ts


class OpeningRangeBreakoutStrategy(Strategy):
    name = "opening_range_breakout"
    eligible_regimes = ALL_KNOWN_REGIMES

    def __init__(self, opening_range_bars: int = DEFAULT_OPENING_RANGE_BARS):
        self.opening_range_bars = opening_range_bars

    def generate(self, df: pd.DataFrame, market_state, mtf=None):
        if not self.is_eligible(market_state):
            return None
        if len(df) == 0:
            return None

        current_day = _date_of(df["timestamp"].iloc[-1])
        todays = df[df["timestamp"].apply(_date_of) == current_day]
        if len(todays) <= self.opening_range_bars:
            return None  # still inside (or before) the opening range itself

        opening_range = todays.iloc[: self.opening_range_bars]
        range_high, range_low = opening_range["high"].max(), opening_range["low"].min()
        width = range_high - range_low
        if width <= 0:
            return None

        close = todays["close"].iloc[-1]
        if close > range_high:
            magnitude = min(1.0, (close - range_high) / width)
            return Signal(self.name, "CE", 0.5 + 0.5 * magnitude,
                          f"Broke above opening range high {range_high:.2f} (range width {width:.2f})")
        if close < range_low:
            magnitude = min(1.0, (range_low - close) / width)
            return Signal(self.name, "PE", 0.5 + 0.5 * magnitude,
                          f"Broke below opening range low {range_low:.2f} (range width {width:.2f})")
        return None
