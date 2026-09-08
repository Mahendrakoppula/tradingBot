"""Support/resistance detection: swing-point (fractal) detection + level
clustering for the swing tier (needs weeks/months of history to find levels
with real touch-count significance), and classic floor-trader pivot points
for the intraday tier (a single-day, no-history-needed calculation, cheap to
recompute across many underlyings every day).

Swing-point detection uses the "fractal" method (a candle whose high/low
exceeds every candle in a window on both sides) - a well-established, simple,
mechanically reliable definition (popularized by Bill Williams), not a
first-cut guess. `tolerance_pct` in `cluster_levels` IS a first-cut,
unbacktested threshold - same caveat class as liquidity.py's MAX_SPREAD_PCT.
"""
from dataclasses import dataclass


@dataclass
class SwingPoint:
    index: int
    price: float
    kind: str  # "high" | "low"
    time: object = None  # whatever the source candle carried (dt.time/dt.datetime), informational only


@dataclass
class PriceLevel:
    price: float
    touches: int
    kind: str  # "high" | "low"
    last_touched_index: int


def find_swing_points(candles: list[dict], left: int = 3, right: int = 3) -> list[SwingPoint]:
    """A candle at index i is a swing high if its `high` strictly exceeds
    every candle's `high` in the `left` candles before AND `right` candles
    after it (symmetric for swing low, using `low`). Requires at least
    left+right+1 candles to find anything."""
    points: list[SwingPoint] = []
    n = len(candles)
    for i in range(left, n - right):
        t = candles[i].get("time", candles[i].get("ts"))
        window_high = candles[i]["high"]
        if all(window_high > candles[j]["high"] for j in range(i - left, i)) and all(
            window_high > candles[j]["high"] for j in range(i + 1, i + right + 1)
        ):
            points.append(SwingPoint(index=i, price=window_high, kind="high", time=t))
            continue  # a candle counted as a swing high isn't also checked as a swing low
        window_low = candles[i]["low"]
        if all(window_low < candles[j]["low"] for j in range(i - left, i)) and all(
            window_low < candles[j]["low"] for j in range(i + 1, i + right + 1)
        ):
            points.append(SwingPoint(index=i, price=window_low, kind="low", time=t))
    return points


def _level_from_group(group: list[SwingPoint], kind: str) -> PriceLevel:
    avg_price = sum(p.price for p in group) / len(group)
    return PriceLevel(price=avg_price, touches=len(group), kind=kind, last_touched_index=max(p.index for p in group))


def cluster_levels(swing_points: list[SwingPoint], tolerance_pct: float = 0.5) -> list[PriceLevel]:
    """Merges swing points of the same kind within `tolerance_pct` of each
    other (percentage distance, e.g. 0.5 = 0.5%) into one PriceLevel - more
    touches means a level more likely to be "real" rather than noise."""
    levels: list[PriceLevel] = []
    for kind in ("high", "low"):
        pts = sorted((p for p in swing_points if p.kind == kind), key=lambda p: p.price)
        group: list[SwingPoint] = []
        for p in pts:
            if group and abs(p.price - group[-1].price) / group[-1].price * 100 > tolerance_pct:
                levels.append(_level_from_group(group, kind))
                group = []
            group.append(p)
        if group:
            levels.append(_level_from_group(group, kind))
    return levels


def classic_pivot_points(prev_high: float, prev_low: float, prev_close: float) -> dict:
    """Standard floor-trader pivot formula (decades-old convention, not a
    guess) off the PRIOR session's high/low/close - PP/R1-3/S1-3."""
    pp = (prev_high + prev_low + prev_close) / 3
    rng = prev_high - prev_low
    return {
        "pp": pp,
        "r1": 2 * pp - prev_low,
        "s1": 2 * pp - prev_high,
        "r2": pp + rng,
        "s2": pp - rng,
        "r3": prev_high + 2 * (pp - prev_low),
        "s3": prev_low - 2 * (prev_high - pp),
    }
