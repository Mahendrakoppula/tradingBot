"""Chart-pattern/structure detectors, deliberately scoped to patterns that
are mechanically detectable with low false-positive risk. Explicitly OUT of
scope for v1: head-and-shoulders, triangles, wedges, flags, cup-and-handle -
these need multi-point geometric fitting with genuinely ambiguous
parameters, and shipping them now would just be another unbacktested guess.
Revisit only once the patterns below have a real backtested/live track
record (see research/backtest_technical.py).
"""
from trading_bot.support_resistance import SwingPoint


def detect_ma_crossover(fast: list[float | None], slow: list[float | None], index: int = -1) -> str | None:
    """Well-established trend-following heuristic, not a first-cut guess.
    Checks the bar at `index` (default: the latest) against the one before
    it. Returns "golden_cross" (fast crosses from at/below to above slow),
    "death_cross" (the reverse), or None."""
    n = len(fast)
    i = index if index >= 0 else n + index
    if i <= 0 or i >= n:
        return None
    f0, f1, s0, s1 = fast[i - 1], fast[i], slow[i - 1], slow[i]
    if None in (f0, f1, s0, s1):
        return None
    if f0 <= s0 and f1 > s1:
        return "golden_cross"
    if f0 >= s0 and f1 < s1:
        return "death_cross"
    return None


def detect_breakout(candles: list[dict], level: float, direction: str, index: int = -1) -> bool:
    """CLOSE-based breakout check (not intrabar-touch) - same
    lookahead-avoidance convention as research/backtest_orb.py's own ORB
    breakout detection. `direction` is "up" or "down"."""
    n = len(candles)
    i = index if index >= 0 else n + index
    if i < 0 or i >= n:
        return False
    close = candles[i]["close"]
    if direction == "up":
        return close > level
    if direction == "down":
        return close < level
    raise ValueError(f"direction must be 'up' or 'down', got {direction!r}")


def detect_trend_structure(swing_points: list[SwingPoint]) -> str | None:
    """Classic Dow-theory structural read: "uptrend" if the last two swing
    highs AND the last two swing lows are both ascending; "downtrend" if
    both descending; None if mixed or there isn't enough history yet. Cheap
    to derive from data support_resistance.find_swing_points already
    computed - well-established structural read, not a first-cut guess."""
    highs = sorted((p for p in swing_points if p.kind == "high"), key=lambda p: p.index)
    lows = sorted((p for p in swing_points if p.kind == "low"), key=lambda p: p.index)
    if len(highs) < 2 or len(lows) < 2:
        return None
    higher_highs = highs[-1].price > highs[-2].price
    higher_lows = lows[-1].price > lows[-2].price
    lower_highs = highs[-1].price < highs[-2].price
    lower_lows = lows[-1].price < lows[-2].price
    if higher_highs and higher_lows:
        return "uptrend"
    if lower_highs and lower_lows:
        return "downtrend"
    return None


def detect_double_top_bottom(swing_points: list[SwingPoint], tolerance_pct: float = 0.5) -> str | None:
    """FIRST-CUT HEURISTIC with real false-positive risk - use only as a
    secondary confirmation signal, never a sole trigger (see
    technical_strategy.py's swing tier). Flags "double_top" when the last
    two swing highs are within `tolerance_pct` of each other with a swing
    low between them (and symmetrically for "double_bottom")."""
    highs = sorted((p for p in swing_points if p.kind == "high"), key=lambda p: p.index)
    lows = sorted((p for p in swing_points if p.kind == "low"), key=lambda p: p.index)
    if len(highs) >= 2:
        h1, h2 = highs[-2], highs[-1]
        if abs(h2.price - h1.price) / h1.price * 100 <= tolerance_pct and any(
            h1.index < lo.index < h2.index for lo in lows
        ):
            return "double_top"
    if len(lows) >= 2:
        l1, l2 = lows[-2], lows[-1]
        if abs(l2.price - l1.price) / l1.price * 100 <= tolerance_pct and any(
            l1.index < hi.index < l2.index for hi in highs
        ):
            return "double_bottom"
    return None
