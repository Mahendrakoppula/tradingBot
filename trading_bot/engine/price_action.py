"""Price-action engine (spec §11): objective candle anatomy and a small set
of mechanically-defined patterns. Everything here is a pure function over
candle dicts and returns numbers/labels - "candles cannot independently
trigger trades" (§11), so nothing here knows about signals or orders.

Thresholds (body/wick ratios, displacement multiple) are first-cut,
publicly-known-textbook defaults, exposed as keyword arguments so the
regime/pre-signal layers (and later M3 validation) can tune them without
touching this module.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class CandleAnatomy:
    range: float
    body: float  # signed: close - open
    upper_wick: float
    lower_wick: float
    body_ratio: float  # |body| / range, 0-1 (0 when range == 0)
    upper_wick_ratio: float
    lower_wick_ratio: float
    close_loc: float  # (close - low) / range, 0 = closed at low, 1 = at high
    rel_size: float | None  # range / ATR, None if ATR unavailable
    displacement: float | None  # |body| / ATR, None if ATR unavailable
    bullish: bool


def anatomy(candle: dict, atr_value: float | None) -> CandleAnatomy:
    o, h, l, c = candle["open"], candle["high"], candle["low"], candle["close"]
    rng = h - l
    body = c - o
    upper = h - max(o, c)
    lower = min(o, c) - l
    safe = rng if rng > 0 else 1.0
    return CandleAnatomy(
        range=rng,
        body=body,
        upper_wick=upper,
        lower_wick=lower,
        body_ratio=abs(body) / safe if rng > 0 else 0.0,
        upper_wick_ratio=upper / safe if rng > 0 else 0.0,
        lower_wick_ratio=lower / safe if rng > 0 else 0.0,
        close_loc=(c - l) / safe if rng > 0 else 0.5,
        rel_size=(rng / atr_value) if atr_value else None,
        displacement=(abs(body) / atr_value) if atr_value else None,
        bullish=c >= o,
    )


def is_doji(a: CandleAnatomy, max_body_ratio: float = 0.1) -> bool:
    return a.range > 0 and a.body_ratio <= max_body_ratio


def is_pin_bar(a: CandleAnatomy, min_wick_ratio: float = 0.6, max_body_ratio: float = 0.3) -> str | None:
    """"bullish_pin" (long lower wick, close in upper part) or "bearish_pin"."""
    if a.range <= 0 or a.body_ratio > max_body_ratio:
        return None
    if a.lower_wick_ratio >= min_wick_ratio and a.close_loc >= 0.6:
        return "bullish_pin"
    if a.upper_wick_ratio >= min_wick_ratio and a.close_loc <= 0.4:
        return "bearish_pin"
    return None


def is_engulfing(prev: dict, cur: dict) -> str | None:
    """Real-body engulfing: current body fully covers the previous body and
    flips direction. Returns "bullish_engulfing" / "bearish_engulfing"."""
    p_o, p_c, c_o, c_c = prev["open"], prev["close"], cur["open"], cur["close"]
    if p_c < p_o and c_c > c_o and c_o <= p_c and c_c >= p_o:
        return "bullish_engulfing"
    if p_c > p_o and c_c < c_o and c_o >= p_c and c_c <= p_o:
        return "bearish_engulfing"
    return None


def is_inside_bar(prev: dict, cur: dict) -> bool:
    return cur["high"] <= prev["high"] and cur["low"] >= prev["low"]


def is_displacement(a: CandleAnatomy, min_atr_multiple: float = 1.5, min_body_ratio: float = 0.6) -> bool:
    """A large-bodied candle relative to ATR - the "displacement" the
    liquidity-sweep / BOS families look for as confirmation."""
    return a.displacement is not None and a.displacement >= min_atr_multiple and a.body_ratio >= min_body_ratio


def is_rejection(a: CandleAnatomy, min_wick_ratio: float = 0.5) -> str | None:
    """Wick-dominated close away from an extreme: "rejection_of_high" or
    "rejection_of_low". Looser than a pin bar (no body cap)."""
    if a.range <= 0:
        return None
    if a.upper_wick_ratio >= min_wick_ratio and a.close_loc <= 0.5:
        return "rejection_of_high"
    if a.lower_wick_ratio >= min_wick_ratio and a.close_loc >= 0.5:
        return "rejection_of_low"
    return None


def is_star(c1: dict, c2: dict, c3: dict, atr_value: float | None, doji_ratio: float = 0.3) -> str | None:
    """Three-candle morning/evening star: strong move, small-bodied pause,
    strong reversal closing past the midpoint of the first body."""
    a1, a2, a3 = anatomy(c1, atr_value), anatomy(c2, atr_value), anatomy(c3, atr_value)
    if a1.body_ratio < 0.5 or a2.body_ratio > doji_ratio or a3.body_ratio < 0.5:
        return None
    mid1 = (c1["open"] + c1["close"]) / 2
    if not a1.bullish and a3.bullish and c3["close"] > mid1:
        return "morning_star"
    if a1.bullish and not a3.bullish and c3["close"] < mid1:
        return "evening_star"
    return None


def detect_patterns(candles: list[dict], i: int, atr_value: float | None) -> list[str]:
    """All pattern labels present at index i, most specific first. Pure
    labels - interpretation in context (trend + regime + location) is the
    pre-signal layer's job."""
    if i < 0 or i >= len(candles):
        return []
    cur = candles[i]
    a = anatomy(cur, atr_value)
    labels: list[str] = []
    if i >= 2:
        star = is_star(candles[i - 2], candles[i - 1], cur, atr_value)
        if star:
            labels.append(star)
    if i >= 1:
        eng = is_engulfing(candles[i - 1], cur)
        if eng:
            labels.append(eng)
        if is_inside_bar(candles[i - 1], cur):
            labels.append("inside_bar")
    pin = is_pin_bar(a)
    if pin:
        labels.append(pin)
    elif is_doji(a):
        labels.append("doji")
    rej = is_rejection(a)
    if rej and pin is None:
        labels.append(rej)
    if is_displacement(a):
        labels.append("displacement")
    return labels
