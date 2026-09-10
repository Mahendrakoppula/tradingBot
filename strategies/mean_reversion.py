from strategies.base import Signal, Strategy

DEFAULT_LOOKBACK = 20
DEFAULT_EDGE_THRESHOLD = 0.15  # fraction of the range's width considered "near the edge"


class MeanReversionStrategy(Strategy):
    name = "mean_reversion"
    eligible_regimes = frozenset({"RANGING"})

    def __init__(self, lookback: int = DEFAULT_LOOKBACK, edge_threshold: float = DEFAULT_EDGE_THRESHOLD):
        self.lookback = lookback
        self.edge_threshold = edge_threshold

    def generate(self, df, market_state, mtf=None):
        if not self.is_eligible(market_state):
            return None
        if len(df) < self.lookback:
            return None

        window = df.iloc[-self.lookback:]
        range_high, range_low = window["high"].max(), window["low"].min()
        width = range_high - range_low
        if width <= 0:
            return None

        close = df["close"].iloc[-1]
        position = (close - range_low) / width  # 0.0 = at the low, 1.0 = at the high

        if position <= self.edge_threshold:
            confidence = min(1.0, 0.5 + (self.edge_threshold - position) / self.edge_threshold)
            return Signal(self.name, "CE", confidence,
                          f"Close at {position:.0%} of {self.lookback}-bar range (near the low) - expecting reversion up")
        if position >= 1 - self.edge_threshold:
            confidence = min(1.0, 0.5 + (position - (1 - self.edge_threshold)) / self.edge_threshold)
            return Signal(self.name, "PE", confidence,
                          f"Close at {position:.0%} of {self.lookback}-bar range (near the high) - expecting reversion down")
        return None
