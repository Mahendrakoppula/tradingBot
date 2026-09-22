"""Market-structure engine (spec §10, §23): swing labelling, BOS/CHoCH/MSS
events, session and day levels, gaps, failed breakouts, liquidity sweeps,
and distance-to-level. Pure functions over candle dicts and SwingPoints.

Promoted from research/framework/market_structure.py (which now re-exports
from here) because the live bot ships only trading_bot/. The lag rule that
module's docstring warned about is made explicit: `visible_swings()` is
the ONLY way callers should obtain swings for a decision at bar i, and it
returns nothing that wasn't fractal-confirmed by bar i.

FIRST-CUT MECHANICAL DEFINITIONS throughout - same honesty convention as
the rest of this repo. Thresholds are keyword arguments so the pre-signal
layer / M3 validation can tune them without editing this module.
"""
from dataclasses import dataclass, field

from trading_bot.chart_patterns import detect_trend_structure
from trading_bot.engine.price_action import anatomy
from trading_bot.support_resistance import SwingPoint, find_swing_points

# --- swings and structure events (promoted) -------------------------------


@dataclass
class StructuralSwing:
    swing: SwingPoint
    label: str | None  # "HH" | "HL" | "LH" | "LL", None if first of its kind


@dataclass
class StructureEvent:
    kind: str  # "bos_up" | "bos_down" | "choch_up" | "choch_down"
    index: int  # the swing point's candle index - only tradeable once confirmed (see visible_swings)
    price: float


def visible_swings(candles: list[dict], i: int, left: int = 3, right: int = 3) -> list[SwingPoint]:
    """Swing points a decision at bar `i` is allowed to see: computed on
    candles[:i+1] only, so a swing at index j exists only if its `right`
    confirming candles have all closed by i. This is the lookahead guard."""
    if i < 0:
        return []
    return find_swing_points(candles[: i + 1], left=left, right=right)


def label_swings(swing_points: list[SwingPoint]) -> list[StructuralSwing]:
    """HH/HL/LH/LL: each high compared to the previous high, each low to
    the previous low, independently, in candle order."""
    ordered = sorted(swing_points, key=lambda p: p.index)
    last_high: float | None = None
    last_low: float | None = None
    labeled: list[StructuralSwing] = []
    for p in ordered:
        if p.kind == "high":
            label = None if last_high is None else ("HH" if p.price > last_high else "LH")
            last_high = p.price
        else:
            label = None if last_low is None else ("HL" if p.price > last_low else "LL")
            last_low = p.price
        labeled.append(StructuralSwing(swing=p, label=label))
    return labeled


_TREND_NORMALISE = {"uptrend": "up", "downtrend": "down", "up": "up", "down": "down", None: None}


def find_structure_events(swing_points: list[SwingPoint]) -> list[StructureEvent]:
    """Walk the labelled sequence with a running trend state. HH while
    up/unknown = bos_up; LL while down/unknown = bos_down; HH while down =
    choch_up; LL while up = choch_down; LH during an uptrend = choch_down;
    HL during a downtrend = choch_up. Before any state exists, seeds from
    chart_patterns.detect_trend_structure (normalised to up/down - the
    research original compared its "uptrend"/"downtrend" strings against
    "up"/"down", a latent mismatch that happened to be unreachable)."""
    labeled = label_swings(swing_points)
    seen: list[SwingPoint] = []
    events: list[StructureEvent] = []
    trend: str | None = None
    for item in labeled:
        prior = trend if trend is not None else _TREND_NORMALISE[detect_trend_structure(seen)]
        seen.append(item.swing)
        if item.label is None:
            continue
        s = item.swing
        if s.kind == "high" and item.label == "HH":
            events.append(StructureEvent("bos_up" if prior in (None, "up") else "choch_up", s.index, s.price))
            trend = "up"
        elif s.kind == "low" and item.label == "LL":
            events.append(StructureEvent("bos_down" if prior in (None, "down") else "choch_down", s.index, s.price))
            trend = "down"
        elif s.kind == "high" and item.label == "LH" and prior == "up":
            events.append(StructureEvent("choch_down", s.index, s.price))
            trend = "down"
        elif s.kind == "low" and item.label == "HL" and prior == "down":
            events.append(StructureEvent("choch_up", s.index, s.price))
            trend = "up"
    return events


def detect_mss(
    events: list[StructureEvent], candles: list[dict], i: int, atr_value: float | None,
    lookback: int = 10, min_displacement_atr: float = 1.0,
) -> str | None:
    """Market Structure Shift: the most recent CHoCH within `lookback` bars
    of i, confirmed by at least one displacement candle in the CHoCH's
    direction between the event and i. Returns "mss_up"/"mss_down"/None.
    A CHoCH alone is an early hint; MSS is the higher-evidence version the
    counter-trend rules (§7) want."""
    recent = [e for e in events if e.kind.startswith("choch") and i - lookback <= e.index <= i]
    if not recent or atr_value is None:
        return None
    e = recent[-1]
    want_bull = e.kind == "choch_up"
    for c in candles[e.index : i + 1]:
        a = anatomy(c, atr_value)
        if a.displacement is not None and a.displacement >= min_displacement_atr and a.body_ratio >= 0.6 and a.bullish == want_bull:
            return "mss_up" if want_bull else "mss_down"
    return None


# --- day / week / session levels (§10: PDH PDL PWH PWL, session hi/lo, OR) --


@dataclass(frozen=True)
class DayLevels:
    pdh: float
    pdl: float
    pdc: float
    pwh: float | None
    pwl: float | None
    # Central Pivot Range + classic R1/S1 from the previous day (the NSE intraday
    # crowd's levels, which is what makes them self-fulfilling): P = (H+L+C)/3,
    # BC = (H+L)/2, TC = 2P - BC, R1 = 2P - L, S1 = 2P - H.
    pivot: float | None = None
    cpr_tc: float | None = None
    cpr_bc: float | None = None
    r1: float | None = None
    s1: float | None = None
    cpr_width_pct: float | None = None  # |TC - BC| as % of the previous close
    cpr_narrow: bool | None = None  # narrower than CPR_NARROW_RATIO x the median of the last CPR_NARROW_LOOKBACK days


CPR_NARROW_LOOKBACK = 10
CPR_NARROW_RATIO = 0.6


def cpr(h: float, l: float, c: float) -> tuple[float, float, float, float, float]:
    """(pivot, tc, bc, r1, s1) for one completed day; tc >= bc by construction."""
    p = (h + l + c) / 3.0
    bc = (h + l) / 2.0
    tc = 2.0 * p - bc
    return p, max(tc, bc), min(tc, bc), 2.0 * p - l, 2.0 * p - h


def day_levels(daily_candles: list[dict]) -> DayLevels | None:
    """Previous-day and previous-week levels from COMPLETED daily bars.
    Callers must pass only bars strictly before today (today's forming
    daily bar is not a level). Previous week = the last full ISO week that
    ends before the last bar's week; None if not enough history. Bars need
    a "date" or "ts" key for the week split."""
    if not daily_candles:
        return None
    prev = daily_candles[-1]
    pwh = pwl = None
    keyed = [(c.get("date") or c["ts"].date(), c) for c in daily_candles if c.get("date") or c.get("ts")]
    if keyed:
        last_week = keyed[-1][0].isocalendar()[:2]
        prev_week = [c for d, c in keyed if d.isocalendar()[:2] < last_week]
        if prev_week:
            target = max(d.isocalendar()[:2] for d, _ in keyed if d.isocalendar()[:2] < last_week)
            week = [c for d, c in keyed if d.isocalendar()[:2] == target]
            pwh = max(c["high"] for c in week)
            pwl = min(c["low"] for c in week)
    pivot, tc, bc, r1, s1 = cpr(prev["high"], prev["low"], prev["close"])
    width_pct = (tc - bc) / prev["close"] * 100.0 if prev["close"] else None
    narrow = None
    hist = daily_candles[-(CPR_NARROW_LOOKBACK + 1):-1]
    if width_pct is not None and len(hist) >= 3:
        widths = sorted(abs(cpr(c["high"], c["low"], c["close"])[1] - cpr(c["high"], c["low"], c["close"])[2]) / c["close"] * 100.0
                        for c in hist if c["close"])
        median = widths[len(widths) // 2]
        narrow = median > 0 and width_pct < CPR_NARROW_RATIO * median
    return DayLevels(pdh=prev["high"], pdl=prev["low"], pdc=prev["close"], pwh=pwh, pwl=pwl,
                     pivot=pivot, cpr_tc=tc, cpr_bc=bc, r1=r1, s1=s1, cpr_width_pct=width_pct, cpr_narrow=narrow)


@dataclass(frozen=True)
class SessionLevels:
    session_high: float
    session_low: float
    or_high: float | None
    or_low: float | None
    or_complete: bool


def session_levels(today_1m: list[dict], opening_range_bars: int = 15) -> SessionLevels | None:
    """Running session high/low and the opening range (first N one-minute
    bars, 15 by default). or_complete is False until N bars exist - the
    ORB family must not act on a partial range."""
    if not today_1m:
        return None
    hi = max(c["high"] for c in today_1m)
    lo = min(c["low"] for c in today_1m)
    if len(today_1m) >= opening_range_bars:
        window = today_1m[:opening_range_bars]
        return SessionLevels(hi, lo, max(c["high"] for c in window), min(c["low"] for c in window), True)
    return SessionLevels(hi, lo, None, None, False)


# --- gaps, failed breakouts, liquidity sweeps ------------------------------


@dataclass(frozen=True)
class Gap:
    size: float  # today_open - prev_close (signed)
    pct: float
    atr_multiple: float | None
    direction: str  # "up" | "down"


def gap(prev_close: float, today_open: float, atr_value: float | None, min_pct: float = 0.1) -> Gap | None:
    size = today_open - prev_close
    pct = size / prev_close * 100.0 if prev_close else 0.0
    if abs(pct) < min_pct:
        return None
    return Gap(size=size, pct=pct, atr_multiple=(abs(size) / atr_value) if atr_value else None,
               direction="up" if size > 0 else "down")


def failed_breakout(candles: list[dict], level: float, i: int, direction: str, lookback: int = 3) -> bool:
    """True at bar i if some close within the previous `lookback` bars was
    beyond `level` in `direction` ("up" = above, "down" = below) and bar i
    closes back on the original side - a breakout that didn't hold."""
    if i < 1:
        return False
    cur = candles[i]["close"]
    prior = candles[max(0, i - lookback) : i]
    if direction == "up":
        return any(c["close"] > level for c in prior) and cur <= level
    if direction == "down":
        return any(c["close"] < level for c in prior) and cur >= level
    raise ValueError(f"direction must be 'up' or 'down', got {direction!r}")


@dataclass(frozen=True)
class Sweep:
    level_name: str
    level: float
    side: str  # "above" (took out a high) | "below" (took out a low)
    excess: float  # how far the wick went beyond the level
    excess_atr: float | None


def liquidity_sweep(
    candle: dict, levels: list[tuple[str, float]], atr_value: float | None, min_excess_atr: float = 0.1,
) -> Sweep | None:
    """A stop-hunt wick: the candle trades beyond a level but CLOSES back on
    the original side. Checks highs against levels above the close and
    lows against levels below. Returns the largest-excess sweep, or None."""
    best: Sweep | None = None
    for name, lvl in levels:
        if candle["high"] > lvl >= candle["close"]:
            excess = candle["high"] - lvl
            side = "above"
        elif candle["low"] < lvl <= candle["close"]:
            excess = lvl - candle["low"]
            side = "below"
        else:
            continue
        excess_atr = (excess / atr_value) if atr_value else None
        if atr_value and excess_atr is not None and excess_atr < min_excess_atr:
            continue
        if best is None or excess > best.excess:
            best = Sweep(name, lvl, side, excess, excess_atr)
    return best


# --- distance to level (§23) ----------------------------------------------


@dataclass(frozen=True)
class LevelDistance:
    name: str
    price: float
    distance: float  # signed: level - price (positive = above)
    distance_atr: float | None


@dataclass(frozen=True)
class DistanceReport:
    nearest_above: LevelDistance | None
    nearest_below: LevelDistance | None
    all: list[LevelDistance] = field(default_factory=list)


def nearest_levels(price: float, levels: list[tuple[str, float]], atr_value: float | None) -> DistanceReport:
    """Signed distances to every named level, plus the nearest above and
    below. The pre-signal "approach to key level" and the no-chase
    "realistic target immediately blocked" checks both read this."""
    dists = [
        LevelDistance(name, lvl, lvl - price, ((lvl - price) / atr_value) if atr_value else None)
        for name, lvl in levels
    ]
    dists.sort(key=lambda d: abs(d.distance))
    above = min((d for d in dists if d.distance > 0), key=lambda d: d.distance, default=None)
    below = max((d for d in dists if d.distance < 0), key=lambda d: d.distance, default=None)
    return DistanceReport(nearest_above=above, nearest_below=below, all=dists)
