"""Market structure: labels the swing-point sequence from
trading_bot.support_resistance.find_swing_points as Higher-High/Higher-Low/
Lower-High/Lower-Low, then walks that labeled sequence to flag Break of
Structure (BOS - a new extreme that continues the prevailing trend) and
Change of Character (CHOCH - a break against the prevailing trend, the
classic early-reversal signal).

FIRST-CUT MECHANICAL DEFINITION, same caveat class as chart_patterns.py's
detect_double_top_bottom: this operates purely on the swing-point LABELS,
not on raw candle closes crossing a level, which keeps it simple and
deterministic but means an event's timing is exactly as fresh as the
underlying swing point (which itself lags by `right` candles per
find_swing_points' fractal confirmation window - a real backtest engine
must account for that lag explicitly, not treat a swing's `index` as
tradeable-at). Reuses chart_patterns.detect_trend_structure for the
"trend so far" read where the walk needs a first opinion before it has
built any state of its own (see find_structure_events).
"""
from dataclasses import dataclass

from trading_bot.chart_patterns import detect_trend_structure
from trading_bot.support_resistance import SwingPoint


@dataclass
class StructuralSwing:
    swing: SwingPoint
    label: str | None  # "HH" | "HL" | "LH" | "LL", None if it's the first of its kind


@dataclass
class StructureEvent:
    kind: str  # "bos_up" | "bos_down" | "choch_up" | "choch_down"
    index: int  # the swing point's candle index (see module docstring re: confirmation lag)
    price: float


def label_swings(swing_points: list[SwingPoint]) -> list[StructuralSwing]:
    """HH/HL/LH/LL labeling: each high is compared to the PREVIOUS high,
    each low to the previous low (independently), ordered by candle
    index."""
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


def find_structure_events(swing_points: list[SwingPoint]) -> list[StructureEvent]:
    """Walks the labeled swing sequence once, tracking a running trend
    state. A new HH (or LL) while already in/entering that trend is a BOS
    (continuation); a new HH while the running trend is DOWN, or a new LL
    while it's UP, is a CHOCH (reversal signal) - and symmetrically, an LH
    breaking an uptrend's most recent HL, or an HL breaking a downtrend's
    most recent LH, is also a CHOCH in the opposite direction. Before any
    trend state of its own has been set, falls back to
    chart_patterns.detect_trend_structure on the swings seen so far."""
    labeled = label_swings(swing_points)
    seen: list[SwingPoint] = []
    events: list[StructureEvent] = []
    trend: str | None = None
    for item in labeled:
        prior_trend = trend if trend is not None else detect_trend_structure(seen)
        seen.append(item.swing)
        if item.label is None:
            continue
        if item.swing.kind == "high" and item.label == "HH":
            kind = "bos_up" if prior_trend in (None, "up") else "choch_up"
            events.append(StructureEvent(kind=kind, index=item.swing.index, price=item.swing.price))
            trend = "up"
        elif item.swing.kind == "low" and item.label == "LL":
            kind = "bos_down" if prior_trend in (None, "down") else "choch_down"
            events.append(StructureEvent(kind=kind, index=item.swing.index, price=item.swing.price))
            trend = "down"
        elif item.swing.kind == "high" and item.label == "LH" and prior_trend == "up":
            events.append(StructureEvent(kind="choch_down", index=item.swing.index, price=item.swing.price))
            trend = "down"
        elif item.swing.kind == "low" and item.label == "HL" and prior_trend == "down":
            events.append(StructureEvent(kind="choch_up", index=item.swing.index, price=item.swing.price))
            trend = "up"
    return events
