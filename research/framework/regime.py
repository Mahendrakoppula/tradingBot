"""Market regime classification: trend direction/strength from Wilder's
ADX/+DI/-DI, and a volatility bucket from where the latest ATR reading
ranks within its own recent history (a percentile rank, not a fixed
threshold - "high volatility" means high FOR THIS underlying recently, not
an absolute number that would mean different things for NIFTY vs a small
stock). Pure function, no state - reuses trading_bot.indicators (adx, atr),
the same indicator math the live bots already use.
"""
from dataclasses import dataclass

from trading_bot.indicators import adx as _adx
from trading_bot.indicators import atr as _atr


@dataclass
class Regime:
    trend_direction: str  # "up" | "down" | "none"
    trend_strength: float | None  # the ADX value itself, None if not enough history
    volatility_bucket: str  # "low" | "normal" | "high" | "unknown"
    plus_di: float | None
    minus_di: float | None
    atr: float | None
    atr_percentile: float | None  # 0-100 rank of the latest ATR within the lookback window


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
    """Classifies the regime as of the LAST candle in the list - callers
    doing a walk-forward/backtest must pass only the candles up to and
    including the bar being evaluated (no full-history shortcut), same
    convention as every other function in this repo that takes an `index`
    or a truncated list rather than mutating a running state object."""
    if not candles:
        return Regime("none", None, "unknown", None, None, None, None)

    adx_line, plus_di_line, minus_di_line = _adx(candles, period=adx_period)
    atr_line = _atr(candles, period=atr_period)
    last = len(candles) - 1

    adx_val = adx_line[last]
    plus_di_val = plus_di_line[last]
    minus_di_val = minus_di_line[last]
    atr_val = atr_line[last]

    if adx_val is None or adx_val < trend_adx_threshold:
        trend_direction = "none"
    elif plus_di_val is not None and minus_di_val is not None and plus_di_val > minus_di_val:
        trend_direction = "up"
    elif plus_di_val is not None and minus_di_val is not None:
        trend_direction = "down"
    else:
        trend_direction = "none"

    window_start = max(0, last - atr_lookback + 1)
    recent_atrs = [v for v in atr_line[window_start : last + 1] if v is not None]
    if atr_val is None or len(recent_atrs) < 2:
        volatility_bucket = "unknown"
        atr_percentile = None
    else:
        rank = sum(1 for v in recent_atrs if v <= atr_val)
        atr_percentile = 100 * rank / len(recent_atrs)
        if atr_percentile <= vol_low_pct:
            volatility_bucket = "low"
        elif atr_percentile >= vol_high_pct:
            volatility_bucket = "high"
        else:
            volatility_bucket = "normal"

    return Regime(
        trend_direction=trend_direction,
        trend_strength=adx_val,
        volatility_bucket=volatility_bucket,
        plus_di=plus_di_val,
        minus_di=minus_di_val,
        atr=atr_val,
        atr_percentile=atr_percentile,
    )
