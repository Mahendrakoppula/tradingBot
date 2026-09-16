"""Market-regime engine (spec §4).

Two layers:

1. `Regime` / `classify_regime` - the research framework's original
   ADX-direction x ATR-percentile read, promoted verbatim (the framework's
   strategies/scoring/backtest_engine consume this shape; the research
   module re-exports it from here).

2. `MarketRegime` / `classify_market_regime` - the spec's 17-label
   classifier with axes (direction / volatility / structure), an explicit
   priority order, transition tracking, and hysteresis so labels don't
   flicker bar to bar. It consumes pre-computed `RegimeInputs` rather than
   raw candles, because several inputs (opening range, PDH/PDL, trend
   score, data quality) come from other engines.

Spec §4: "Do not assume a regime is profitable. Measure regime-specific
expectancy." Every threshold here is a first-cut in RegimeConfig.
"""
from dataclasses import dataclass, field

from trading_bot.indicators import adx as _adx
from trading_bot.indicators import atr as _atr

# --- layer 1: promoted basic regime (interface preserved) --------------------


@dataclass
class Regime:
    trend_direction: str  # "up" | "down" | "none"
    trend_strength: float | None  # ADX
    volatility_bucket: str  # "low" | "normal" | "high" | "unknown"
    plus_di: float | None
    minus_di: float | None
    atr: float | None
    atr_percentile: float | None


def classify_regime(
    candles: list[dict],
    *,
    adx_period: int = 14,
    atr_period: int = 14,
    atr_lookback: int = 100,
    trend_adx_threshold: float = 20.0,
    vol_low_pct: float = 33.0,
    vol_high_pct: float = 67.0,
) -> Regime:
    """Regime as of the LAST candle - pass only candles up to the bar being
    evaluated (walk-forward convention)."""
    if not candles:
        return Regime("none", None, "unknown", None, None, None, None)
    adx_line, plus_di_line, minus_di_line = _adx(candles, period=adx_period)
    atr_line = _atr(candles, period=atr_period)
    last = len(candles) - 1
    adx_val, p, m, atr_val = adx_line[last], plus_di_line[last], minus_di_line[last], atr_line[last]
    if adx_val is None or adx_val < trend_adx_threshold:
        direction = "none"
    elif p is not None and m is not None and p > m:
        direction = "up"
    elif p is not None and m is not None:
        direction = "down"
    else:
        direction = "none"
    recent = [v for v in atr_line[max(0, last - atr_lookback + 1) : last + 1] if v is not None]
    if atr_val is None or len(recent) < 2:
        bucket, pct = "unknown", None
    else:
        pct = 100 * sum(1 for v in recent if v <= atr_val) / len(recent)
        bucket = "low" if pct <= vol_low_pct else "high" if pct >= vol_high_pct else "normal"
    return Regime(direction, adx_val, bucket, p, m, atr_val, pct)


# --- layer 2: the spec's 17-label market regime ----------------------------

REGIME_LABELS: tuple[str, ...] = (
    "STRONG_BULL", "BULL", "WEAK_BULL", "STRONG_BEAR", "BEAR", "WEAK_BEAR",
    "RANGE", "BREAKOUT", "BREAKDOWN", "HIGH_VOLATILITY", "LOW_VOLATILITY",
    "COMPRESSION", "EXPANSION", "CHOPPY", "TRANSITION", "UNSTABLE", "NO_TRADE",
)


@dataclass(frozen=True)
class RegimeConfig:
    adx_trend_min: float = 20.0
    adx_choppy_max: float = 15.0
    atr_pct_high: float = 80.0
    atr_pct_low: float = 20.0
    bb_width_pct_compression: float = 20.0  # percentile rank of BB width
    bb_width_pct_expansion: float = 80.0
    atr_pct_compression: float = 30.0
    choppy_min_ema_crosses: int = 3
    expansion_range_atr: float = 1.5  # current bar range / ATR
    open_quiet_minutes: int = 5
    hysteresis_bars: int = 2


@dataclass(frozen=True)
class RegimeInputs:
    quality_ok: bool
    minutes_since_open: int
    trend_score: float  # from the 5m TFTrend, [-1, 1]
    trend_label: str
    trend_transition: bool
    trend_unstable: bool
    adx: float | None
    atr_percentile: float | None
    bb_width_percentile: float | None
    ema_fast_cross_count: int  # close/EMA20 crosses in the recent lookback
    close: float
    prev_close: float
    bar_range_atr: float | None  # (high-low)/ATR of the current bar
    opening_range: tuple[float, float] | None  # (hi, lo), None until complete
    pdh: float | None
    pdl: float | None
    in_swing_range: bool  # close inside the last confirmed swing high/low


@dataclass(frozen=True)
class MarketRegime:
    primary: str
    candidate: str  # what this bar alone says; becomes primary after hysteresis
    direction_axis: str  # "BULL" | "BEAR" | "NONE"
    volatility_axis: str  # "HIGH" | "LOW" | "NORMAL" | "UNKNOWN"
    structure_axis: str  # "TREND" | "RANGE" | "BREAKOUT" | "BREAKDOWN" | "COMPRESSION" | "EXPANSION" | "CHOPPY" | "NONE"
    transition: str | None  # e.g. "RANGE->BULL" on the bar the primary changes
    pending_bars: int
    evidence: dict = field(default_factory=dict)


def _axes(x: RegimeInputs, cfg: RegimeConfig) -> tuple[str, str, str]:
    if x.trend_score >= 0.2 and (x.adx or 0) >= cfg.adx_trend_min:
        direction = "BULL"
    elif x.trend_score <= -0.2 and (x.adx or 0) >= cfg.adx_trend_min:
        direction = "BEAR"
    else:
        direction = "NONE"
    if x.atr_percentile is None:
        vol = "UNKNOWN"
    elif x.atr_percentile >= cfg.atr_pct_high:
        vol = "HIGH"
    elif x.atr_percentile <= cfg.atr_pct_low:
        vol = "LOW"
    else:
        vol = "NORMAL"
    structure = "NONE"
    broke_up = broke_down = False
    if x.opening_range is not None:
        broke_up |= x.close > x.opening_range[0] >= x.prev_close
        broke_down |= x.close < x.opening_range[1] <= x.prev_close
    if x.pdh is not None:
        broke_up |= x.close > x.pdh >= x.prev_close
    if x.pdl is not None:
        broke_down |= x.close < x.pdl <= x.prev_close
    expanding = x.bar_range_atr is not None and x.bar_range_atr >= cfg.expansion_range_atr
    compressed = (
        x.bb_width_percentile is not None and x.bb_width_percentile <= cfg.bb_width_pct_compression
        and x.atr_percentile is not None and x.atr_percentile <= cfg.atr_pct_compression
    )
    if broke_up and expanding:
        structure = "BREAKOUT"
    elif broke_down and expanding:
        structure = "BREAKDOWN"
    elif compressed:
        structure = "COMPRESSION"
    elif x.bb_width_percentile is not None and x.bb_width_percentile >= cfg.bb_width_pct_expansion and expanding:
        structure = "EXPANSION"
    elif (x.adx or 0) < cfg.adx_choppy_max and x.ema_fast_cross_count >= cfg.choppy_min_ema_crosses:
        structure = "CHOPPY"
    elif (x.adx or 0) < cfg.adx_trend_min and x.in_swing_range:
        structure = "RANGE"
    elif direction != "NONE":
        structure = "TREND"
    return direction, vol, structure


def _candidate(x: RegimeInputs, cfg: RegimeConfig, direction: str, vol: str, structure: str) -> str:
    """Priority order from the plan: safety labels first, then structural
    events, then volatility extremes, then chop/range, then trend."""
    if not x.quality_ok or x.minutes_since_open < cfg.open_quiet_minutes:
        return "NO_TRADE"
    if x.trend_unstable or x.trend_label == "UNSTABLE":
        return "UNSTABLE"
    if x.trend_transition or x.trend_label == "TREND_TRANSITION":
        return "TRANSITION"
    if structure in ("BREAKOUT", "BREAKDOWN", "COMPRESSION", "EXPANSION"):
        return structure
    if vol == "HIGH":
        return "HIGH_VOLATILITY"
    if vol == "LOW" and direction == "NONE":
        return "LOW_VOLATILITY"
    if structure == "CHOPPY":
        return "CHOPPY"
    if structure == "RANGE":
        return "RANGE"
    if direction != "NONE":
        mag = abs(x.trend_score)
        strong = mag >= 0.7 and (x.adx or 0) >= cfg.adx_trend_min + 5
        if strong:
            return "STRONG_" + direction
        if mag >= 0.4:
            return direction
        return "WEAK_" + direction
    return "RANGE" if x.in_swing_range else "LOW_VOLATILITY" if vol == "LOW" else "TRANSITION"


IMMEDIATE_LABELS = {"NO_TRADE", "UNSTABLE"}  # safety labels bypass hysteresis


def classify_market_regime(x: RegimeInputs, prev: MarketRegime | None, cfg: RegimeConfig) -> MarketRegime:
    direction, vol, structure = _axes(x, cfg)
    candidate = _candidate(x, cfg, direction, vol, structure)
    evidence = {"direction": direction, "volatility": vol, "structure": structure, "adx": x.adx,
                "atr_pct": x.atr_percentile, "bb_width_pct": x.bb_width_percentile, "trend_score": x.trend_score}

    if prev is None:
        return MarketRegime(candidate, candidate, direction, vol, structure, None, 0, evidence)

    if candidate == prev.primary:
        return MarketRegime(prev.primary, candidate, direction, vol, structure, None, 0, evidence)

    if candidate in IMMEDIATE_LABELS:
        return MarketRegime(candidate, candidate, direction, vol, structure, f"{prev.primary}->{candidate}", 0, evidence)

    pending = prev.pending_bars + 1 if prev.candidate == candidate else 1
    if pending >= cfg.hysteresis_bars:
        return MarketRegime(candidate, candidate, direction, vol, structure, f"{prev.primary}->{candidate}", 0, evidence)
    return MarketRegime(prev.primary, candidate, direction, vol, structure, None, pending, evidence)
