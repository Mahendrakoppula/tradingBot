"""Pure-Python technical indicator math over plain candle dicts/lists - no
numpy/pandas dependency, consistent with the rest of this repo (see
research/backtest_orb.py's own stdlib-only style). Every function returns a
list the same length as its input, index-aligned, with `None` wherever there
isn't yet enough history to compute a value - callers filter/guard on that
rather than the function silently shortening its output.

Candle dicts follow the shape used throughout this repo (research/
backtest_orb.py's load_candles(), candle_history_logger.py): at minimum
`open/high/low/close`, optionally `volume` (defaults to 0 if absent).

Formulas are the standard, decades-old textbook definitions (SMA, EMA,
Wilder's RSI/ATR smoothing, MACD, VWAP) - not first-cut guesses. What IS
still unvalidated is which periods/thresholds a given strategy tier chooses
to use them with - see technical_strategy.py / research/backtest_technical.py
for that.
"""


def sma(values: list[float], period: int) -> list[float | None]:
    n = len(values)
    result: list[float | None] = [None] * n
    if period <= 0 or n < period:
        return result
    window_sum = sum(values[:period])
    result[period - 1] = window_sum / period
    for i in range(period, n):
        window_sum += values[i] - values[i - period]
        result[i] = window_sum / period
    return result


def _ema_skip_leading_none(values: list[float | None], period: int) -> list[float | None]:
    """EMA seeded by a plain SMA of the first `period` values, starting from
    wherever the input's first non-None value is (so this doubles as the
    implementation for both `ema()` - no leading Nones - and for smoothing
    an already-partial series like a MACD line)."""
    n = len(values)
    result: list[float | None] = [None] * n
    start = next((i for i, v in enumerate(values) if v is not None), None)
    if start is None or n - start < period:
        return result
    multiplier = 2 / (period + 1)
    window = values[start : start + period]
    seed = sum(window) / period  # type: ignore[arg-type]
    idx = start + period - 1
    result[idx] = seed
    prev = seed
    for i in range(idx + 1, n):
        prev = (values[i] - prev) * multiplier + prev  # type: ignore[operator]
        result[i] = prev
    return result


def ema(values: list[float], period: int) -> list[float | None]:
    return _ema_skip_leading_none(values, period)


def rsi(closes: list[float], period: int = 14) -> list[float | None]:
    """Wilder's RSI - the standard formulation (smoothed running average of
    gains/losses), not a naive simple-average variant."""
    n = len(closes)
    result: list[float | None] = [None] * n
    if n <= period:
        return result
    deltas = [closes[i] - closes[i - 1] for i in range(1, n)]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]

    def _rsi_value(avg_gain: float, avg_loss: float) -> float:
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - 100 / (1 + rs)

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    result[period] = _rsi_value(avg_gain, avg_loss)
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        result[i + 1] = _rsi_value(avg_gain, avg_loss)
    return result


def macd(
    closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Returns (macd_line, signal_line, histogram), each index-aligned to
    `closes`. macd_line = EMA(fast) - EMA(slow); signal_line = EMA(macd_line,
    signal); histogram = macd_line - signal_line."""
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_line: list[float | None] = [
        (f - s) if (f is not None and s is not None) else None for f, s in zip(ema_fast, ema_slow)
    ]
    signal_line = _ema_skip_leading_none(macd_line, signal)
    histogram: list[float | None] = [
        (m - s) if (m is not None and s is not None) else None for m, s in zip(macd_line, signal_line)
    ]
    return macd_line, signal_line, histogram


def atr(candles: list[dict], period: int = 14) -> list[float | None]:
    """Wilder-smoothed Average True Range - used both as a volatility read
    and (by callers) to size stop-losses. True range needs the PRIOR
    candle's close, so the first possible value is at index `period` (one
    fewer true-range observation than candles)."""
    n = len(candles)
    result: list[float | None] = [None] * n
    if n <= period:
        return result
    trs = []
    for i in range(1, n):
        h, l, prev_c = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
    avg = sum(trs[:period]) / period
    result[period] = avg
    for i in range(period, len(trs)):
        avg = (avg * (period - 1) + trs[i]) / period
        result[i + 1] = avg
    return result


def vwap(candles: list[dict]) -> list[float]:
    """Cumulative (typical price x volume) / cumulative volume from the
    START of the given candle list - this is an INTRADAY-only indicator that
    resets each session, so callers must pass exactly one day's candles, not
    a multi-day series (unlike every other function in this module, which is
    happy to take an arbitrarily long history)."""
    result = []
    cum_pv = 0.0
    cum_vol = 0.0
    for c in candles:
        typical = (c["high"] + c["low"] + c["close"]) / 3
        vol = c.get("volume", 0) or 0
        cum_pv += typical * vol
        cum_vol += vol
        result.append(cum_pv / cum_vol if cum_vol > 0 else c["close"])
    return result


def rolling_avg_volume(candles: list[dict], period: int) -> list[float | None]:
    return sma([c.get("volume", 0) or 0 for c in candles], period)
