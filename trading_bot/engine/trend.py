"""Market-trend engine (spec §5-§8): a per-timeframe trend read with the
spec's 10 labels, plus the weighted multi-timeframe alignment model.

Trend is CONTEXT - "context + directional preference + strategy-routing
factor", never a standalone entry signal (§6, §8). Nothing here emits a
signal; it produces labels, a [-1, 1] score and an evidence dict that the
regime, pre-signal and (in M2) routing/scoring layers consume.

Evidence blended into the score, each mapped to [-1, 1]:
  structure  - last BOS/CHoCH direction from the confirmed swing sequence
  ema_order  - close vs EMA20 vs EMA50 vs EMA200 stacking
  slope      - ATR-normalised EMA20 slope
  momentum   - RSI distance from 50 and MACD-histogram sign
  di         - (+DI - -DI) / (+DI + -DI)
  vwap       - close vs session VWAP (intraday TFs, when a real VWAP exists)
The weights and label thresholds are FIRST-CUT and unvalidated (§8: "these
weights must remain configurable and be validated statistically") - they
live in TrendConfig so M3 can move them without touching this module.
"""
from dataclasses import dataclass, field

from trading_bot.engine.structure import find_structure_events, visible_swings
from trading_bot.indicators import adx, atr, ema, macd, rsi, slope

TREND_LABELS: tuple[str, ...] = (
    "STRONG_BULL", "BULL", "WEAK_BULL", "NEUTRAL", "WEAK_BEAR", "BEAR", "STRONG_BEAR",
    "COUNTER_TREND", "TREND_TRANSITION", "UNSTABLE",
)
ALIGNMENT_LABELS: tuple[str, ...] = (
    "STRONG_TREND_ALIGNMENT", "TREND_ALIGNMENT", "WEAK_ALIGNMENT", "NEUTRAL", "COUNTER_TREND", "TREND_TRANSITION",
)

# Direction magnitude each label contributes to alignment (spec §8 model).
LABEL_DIRECTION: dict[str, float] = {
    "STRONG_BULL": 1.0, "BULL": 0.66, "WEAK_BULL": 0.33, "NEUTRAL": 0.0,
    "WEAK_BEAR": -0.33, "BEAR": -0.66, "STRONG_BEAR": -1.0,
    # the three "state" labels carry their underlying score's sign at reduced weight
    "COUNTER_TREND": 0.0, "TREND_TRANSITION": 0.0, "UNSTABLE": 0.0,
}


@dataclass(frozen=True)
class TrendConfig:
    ema_fast: int = 20
    ema_mid: int = 50
    ema_slow: int = 200
    adx_period: int = 14
    adx_min: float = 20.0
    adx_strong: float = 25.0
    rsi_period: int = 14
    atr_period: int = 14
    slope_period: int = 5
    swing_left: int = 3
    swing_right: int = 3
    transition_lookback: int = 5
    unstable_flips: int = 3
    unstable_window: int = 6
    exhaustion_rsi: float = 75.0
    w_structure: float = 0.30
    w_ema_order: float = 0.25
    w_slope: float = 0.15
    w_momentum: float = 0.15
    w_di: float = 0.15
    w_vwap: float = 0.10  # only applied when a VWAP value exists; renormalised


@dataclass(frozen=True)
class TFTrend:
    tf: str
    label: str
    score: float  # [-1, 1]
    strength: float | None  # ADX
    persistence_bars: int
    acceleration: float  # score - previous score
    exhaustion: bool
    transition: bool
    evidence: dict = field(default_factory=dict)

    @property
    def direction(self) -> str:
        return "up" if self.score > 0 else ("down" if self.score < 0 else "none")


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _last(series: list, i: int):
    return series[i] if 0 <= i < len(series) else None


def classify_tf_trend(
    candles: list[dict],
    tf: str,
    cfg: TrendConfig,
    *,
    i: int | None = None,
    prev: TFTrend | None = None,
    higher: TFTrend | None = None,
    recent_labels: list[str] | None = None,
    vwap_value: float | None = None,
) -> TFTrend:
    """Trend as of bar `i` (default: last bar), using only candles[:i+1].
    `prev` is this TF's previous read (for persistence/acceleration),
    `higher` the next-higher timeframe's current read (for COUNTER_TREND),
    `recent_labels` this TF's last few labels (for UNSTABLE)."""
    if i is None:
        i = len(candles) - 1
    window = candles[: i + 1]
    n = len(window)
    if n < cfg.ema_fast + 2:
        return TFTrend(tf, "NEUTRAL", 0.0, None, 0, 0.0, False, False, {"reason": "insufficient_history", "bars": n})

    closes = [c["close"] for c in window]
    close = closes[-1]
    e_fast, e_mid, e_slow = ema(closes, cfg.ema_fast), ema(closes, cfg.ema_mid), ema(closes, cfg.ema_slow)
    atr_line = atr(window, cfg.atr_period)
    adx_line, pdi, mdi = adx(window, cfg.adx_period)
    rsi_line = rsi(closes, cfg.rsi_period)
    _, _, hist = macd(closes)
    last = n - 1
    ev: dict = {}

    # structure
    swings = visible_swings(window, last, cfg.swing_left, cfg.swing_right)
    events = find_structure_events(swings)
    structure = 0.0
    transition = False
    if events:
        e = events[-1]
        structure = 1.0 if e.kind in ("bos_up", "choch_up") else -1.0
        if e.kind.startswith("choch"):
            structure *= 0.6  # fresh reversal evidence, not yet a trend
        ev["last_event"] = e.kind
    ev["structure"] = structure

    # ema order: three pairwise relations, each +1/-1
    rel = 0
    pairs = [(close, e_fast[last]), (e_fast[last], e_mid[last]), (e_mid[last], e_slow[last])]
    counted = 0
    for a, b in pairs:
        if a is not None and b is not None:
            rel += 1 if a > b else -1
            counted += 1
    ema_order = rel / counted if counted else 0.0
    ev["ema_order"] = ema_order

    # slope of EMA20 in ATR units over slope_period, clipped at +-1 ATR
    sl = slope(e_fast, cfg.slope_period, atr_line)
    slope_v = _clip(sl[last] or 0.0) if _last(sl, last) is not None else 0.0
    ev["ema_slope_atr"] = _last(sl, last)

    # momentum
    r = _last(rsi_line, last)
    h = _last(hist, last)
    mom = 0.0
    if r is not None:
        mom = _clip((r - 50.0) / 50.0)
    if h is not None:
        mom = _clip(mom + (0.25 if h > 0 else -0.25 if h < 0 else 0.0))
    ev["rsi"] = r
    ev["macd_hist"] = h

    # directional index
    p, m = _last(pdi, last), _last(mdi, last)
    di = 0.0
    if p is not None and m is not None and (p + m) > 0:
        di = (p - m) / (p + m)
    strength = _last(adx_line, last)
    ev["adx"] = strength

    weights = [cfg.w_structure, cfg.w_ema_order, cfg.w_slope, cfg.w_momentum, cfg.w_di]
    parts = [structure, ema_order, slope_v, mom, di]
    if vwap_value is not None and tf != "1d":
        parts.append(1.0 if close > vwap_value else -1.0 if close < vwap_value else 0.0)
        weights.append(cfg.w_vwap)
        ev["vwap_side"] = parts[-1]
    total_w = sum(weights)
    score = _clip(sum(w * p_ for w, p_ in zip(weights, parts)) / total_w)

    # base label
    mag = abs(score)
    if mag >= 0.7 and strength is not None and strength >= cfg.adx_strong:
        base = "STRONG_BULL" if score > 0 else "STRONG_BEAR"
    elif mag >= 0.4:
        base = "BULL" if score > 0 else "BEAR"
    elif mag >= 0.2:
        base = "WEAK_BULL" if score > 0 else "WEAK_BEAR"
    else:
        base = "NEUTRAL"

    # state overrides (priority: UNSTABLE > TRANSITION > COUNTER_TREND)
    label = base
    if events:
        e = events[-1]
        against = (e.kind.startswith("choch")) and ((e.kind == "choch_down" and score > 0) or (e.kind == "choch_up" and score < 0))
        if against and last - e.index <= cfg.transition_lookback:
            transition = True
    flips = 0
    if recent_labels:
        signs = [_sign_of_label(l) for l in recent_labels[-cfg.unstable_window :]] + [_sign_of_label(base)]
        signs = [s for s in signs if s != 0]
        flips = sum(1 for a, b in zip(signs, signs[1:]) if a != b)
    if flips >= cfg.unstable_flips:
        label = "UNSTABLE"
    elif transition:
        label = "TREND_TRANSITION"
    elif higher is not None and abs(higher.score) >= 0.5 and score != 0 and (higher.score > 0) != (score > 0) and mag >= 0.2:
        label = "COUNTER_TREND"
    ev["base_label"] = base
    ev["flips"] = flips

    persistence = 1
    accel = score
    if prev is not None:
        accel = score - prev.score
        persistence = prev.persistence_bars + 1 if (prev.score > 0) == (score > 0) and prev.score != 0 and score != 0 else 1
    exhaustion = bool(
        mag >= 0.7 and r is not None and (r >= cfg.exhaustion_rsi or r <= 100 - cfg.exhaustion_rsi)
        and prev is not None and abs(score) < abs(prev.score)
    )
    return TFTrend(tf, label, round(score, 4), strength, persistence, round(accel, 4), exhaustion, transition, ev)


def _sign_of_label(label: str) -> int:
    v = LABEL_DIRECTION.get(label, 0.0)
    return 1 if v > 0 else -1 if v < 0 else 0


@dataclass(frozen=True)
class Alignment:
    label: str
    weighted_score: float  # [-1, 1]
    direction_preference: str  # "up" | "down" | "none"
    per_tf: dict = field(default_factory=dict)


def align(trends: dict[str, TFTrend], weights: dict[str, float]) -> Alignment:
    """Spec §8 alignment: weighted sum of each TF's label direction value.
    COUNTER_TREND when a lower TF opposes a higher TF that has |score|>=.5;
    TREND_TRANSITION when the 30m or 5m read is itself in transition."""
    order = ["1d", "30m", "5m", "1m"]
    present = [tf for tf in order if tf in trends]
    if not present:
        return Alignment("NEUTRAL", 0.0, "none", {})
    total_w = sum(weights.get(tf, 0.0) for tf in present) or 1.0
    per_tf = {tf: {"label": trends[tf].label, "score": trends[tf].score, "value": _label_value(trends[tf])} for tf in present}
    weighted = sum(weights.get(tf, 0.0) * per_tf[tf]["value"] for tf in present) / total_w

    if any(trends[tf].label == "TREND_TRANSITION" for tf in ("30m", "5m") if tf in trends):
        label = "TREND_TRANSITION"
    elif _counter_trend(trends, present):
        label = "COUNTER_TREND"
    else:
        mag = abs(weighted)
        same_sign = len({1 if per_tf[tf]["value"] > 0 else -1 for tf in present if per_tf[tf]["value"] != 0}) <= 1
        if mag >= 0.75 and same_sign:
            label = "STRONG_TREND_ALIGNMENT"
        elif mag >= 0.5:
            label = "TREND_ALIGNMENT"
        elif mag >= 0.25:
            label = "WEAK_ALIGNMENT"
        else:
            label = "NEUTRAL"
    pref = "up" if weighted > 0.1 else "down" if weighted < -0.1 else "none"
    return Alignment(label, round(weighted, 4), pref, per_tf)


def _label_value(t: TFTrend) -> float:
    v = LABEL_DIRECTION.get(t.label)
    if v is None or t.label in ("COUNTER_TREND", "TREND_TRANSITION", "UNSTABLE"):
        return _clip(t.score) * 0.5  # state labels: half-weight the raw score
    return v


def _counter_trend(trends: dict[str, TFTrend], present: list[str]) -> bool:
    for hi_idx in range(len(present)):
        hi = trends[present[hi_idx]]
        if abs(hi.score) < 0.5:
            continue
        for lo_tf in present[hi_idx + 1 :]:
            lo = trends[lo_tf]
            if abs(lo.score) >= 0.2 and (lo.score > 0) != (hi.score > 0):
                return True
    return False
