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
to use them with - see research/backtest_technical.py and research/framework/
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


def adx(
    candles: list[dict], period: int = 14
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Wilder's ADX (Average Directional Index), plus the +DI/-DI lines it's
    built from - returns (adx_line, plus_di, minus_di), each index-aligned to
    `candles`. Standard textbook formulation (directional movement -> Wilder
    smoothing -> DX -> Wilder-smoothed ADX), same convention as `atr`/`rsi`
    above. Needs more than 2*period candles before the first ADX value
    (period bars to seed the smoothed +DM/-DM/TR, another period to smooth
    DX into ADX)."""
    n = len(candles)
    none_col: list[float | None] = [None] * n
    if n <= 2 * period:
        return none_col, list(none_col), list(none_col)

    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    trs = [0.0] * n
    for i in range(1, n):
        up_move = candles[i]["high"] - candles[i - 1]["high"]
        down_move = candles[i - 1]["low"] - candles[i]["low"]
        plus_dm[i] = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm[i] = down_move if (down_move > up_move and down_move > 0) else 0.0
        h, l, prev_c = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        trs[i] = max(h - l, abs(h - prev_c), abs(l - prev_c))

    def _wilder_smooth(values: list[float], start: int) -> list[float | None]:
        result: list[float | None] = [None] * n
        seed = sum(values[start + 1 : start + 1 + period])
        idx = start + period
        result[idx] = seed
        prev = seed
        for i in range(idx + 1, n):
            prev = prev - (prev / period) + values[i]
            result[i] = prev
        return result

    smoothed_tr = _wilder_smooth(trs, 0)
    smoothed_plus_dm = _wilder_smooth(plus_dm, 0)
    smoothed_minus_dm = _wilder_smooth(minus_dm, 0)

    plus_di: list[float | None] = list(none_col)
    minus_di: list[float | None] = list(none_col)
    dx: list[float | None] = list(none_col)
    for i in range(period, n):
        tr_i, pdm_i, mdm_i = smoothed_tr[i], smoothed_plus_dm[i], smoothed_minus_dm[i]
        if tr_i is None or tr_i == 0:
            continue
        pdi = 100 * pdm_i / tr_i
        mdi = 100 * mdm_i / tr_i
        plus_di[i] = pdi
        minus_di[i] = mdi
        denom = pdi + mdi
        dx[i] = 100 * abs(pdi - mdi) / denom if denom else 0.0

    adx_line = _wilder_smooth_from_partial(dx, period)
    return adx_line, plus_di, minus_di


def _wilder_smooth_from_partial(values: list[float | None], period: int) -> list[float | None]:
    """Same Wilder recurrence as `_wilder_smooth` inside `adx`, but for a
    series (DX) that already has leading Nones before `period` and whose
    seed is an average, not a sum - used only by `adx` for the DX->ADX step."""
    n = len(values)
    result: list[float | None] = [None] * n
    first_valid = next((i for i, v in enumerate(values) if v is not None), None)
    if first_valid is None or n - first_valid < period:
        return result
    window = values[first_valid : first_valid + period]
    seed = sum(window) / period  # type: ignore[arg-type]
    idx = first_valid + period - 1
    result[idx] = seed
    prev = seed
    for i in range(idx + 1, n):
        prev = (prev * (period - 1) + values[i]) / period  # type: ignore[operator]
        result[i] = prev
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


# --- additions for the index-options engine (spec §12) -------------------
# Same conventions as everything above: index-aligned lists, None until
# enough history, standard textbook formulas. Which periods a given engine
# uses is config, not code (trading_bot/engine/config.py).


def bollinger(
    closes: list[float], period: int = 20, num_std: float = 2.0
) -> tuple[list[float | None], list[float | None], list[float | None], list[float | None]]:
    """Bollinger Bands on a population standard deviation (the common
    charting convention). Returns (middle, upper, lower, width_pct) where
    width_pct = (upper - lower) / middle * 100 - the "Bollinger width" the
    regime engine uses for compression/expansion (spec §4)."""
    n = len(closes)
    mid = sma(closes, period)
    upper: list[float | None] = [None] * n
    lower: list[float | None] = [None] * n
    width: list[float | None] = [None] * n
    for i in range(period - 1, n):
        m = mid[i]
        if m is None:
            continue
        window = closes[i - period + 1 : i + 1]
        var = sum((x - m) ** 2 for x in window) / period
        sd = var ** 0.5
        upper[i] = m + num_std * sd
        lower[i] = m - num_std * sd
        width[i] = ((upper[i] - lower[i]) / m * 100.0) if m else None
    return mid, upper, lower, width


def obv(candles: list[dict]) -> list[float]:
    """On-balance volume: cumulative signed volume by close-to-close
    direction. Meaningless on index candles (volume=0) - the engine feeds it
    the futures proxy series instead (see engine/config.py volume_proxy)."""
    result: list[float] = []
    total = 0.0
    for i, c in enumerate(candles):
        vol = c.get("volume", 0) or 0
        if i > 0:
            prev_close = candles[i - 1]["close"]
            if c["close"] > prev_close:
                total += vol
            elif c["close"] < prev_close:
                total -= vol
        result.append(total)
    return result


def percentile_rank_series(values: list[float | None], lookback: int) -> list[float | None]:
    """For each index, the percentile (0-100) of values[i] within the
    trailing `lookback` window ending at i (inclusive) - the fraction of
    window values strictly below it. Used for ATR percentile (spec §4/§12).
    None where fewer than `lookback` non-None values are available."""
    n = len(values)
    result: list[float | None] = [None] * n
    for i in range(n):
        v = values[i]
        if v is None:
            continue
        window = [x for x in values[max(0, i - lookback + 1) : i + 1] if x is not None]
        if len(window) < lookback:
            continue
        below = sum(1 for x in window if x < v)
        result[i] = below / len(window) * 100.0
    return result


def relative_volume_series(candles: list[dict], period: int) -> list[float | None]:
    """volume / trailing average volume (the average EXCLUDES the current
    bar, so a spike doesn't dilute its own baseline). None until `period`
    prior bars exist or when the baseline is 0."""
    n = len(candles)
    result: list[float | None] = [None] * n
    vols = [float(c.get("volume", 0) or 0) for c in candles]
    for i in range(period, n):
        base = sum(vols[i - period : i]) / period
        result[i] = (vols[i] / base) if base > 0 else None
    return result


def slope(values: list[float | None], period: int, atr_series: list[float | None] | None = None) -> list[float | None]:
    """Change in a series over `period` bars, optionally normalised by ATR
    so slopes are comparable across instruments and volatility regimes
    (spec §4-5 "EMA slope"). slope[i] = (v[i] - v[i-period]) / (ATR[i] or 1)."""
    n = len(values)
    result: list[float | None] = [None] * n
    for i in range(period, n):
        a, b = values[i - period], values[i]
        if a is None or b is None:
            continue
        denom = 1.0
        if atr_series is not None:
            av = atr_series[i]
            if av is None or av <= 0:
                continue
            denom = av
        result[i] = (b - a) / denom
    return result


def session_vwap(candles: list[dict]) -> list[float | None]:
    """VWAP that resets at each new session (date change), so it can be
    computed over a multi-day 1m/5m series without the caller slicing per
    day. Candles need a "ts" (datetime) or "date" key. None where the
    session's cumulative volume is 0 (e.g. index candles) - the caller then
    falls back to the futures proxy rather than a fake VWAP equal to close."""
    result: list[float | None] = []
    cur_day = None
    cum_pv = cum_vol = 0.0
    for c in candles:
        day = c.get("date") or (c["ts"].date() if c.get("ts") is not None else None)
        if day != cur_day:
            cur_day, cum_pv, cum_vol = day, 0.0, 0.0
        vol = float(c.get("volume", 0) or 0)
        cum_pv += (c["high"] + c["low"] + c["close"]) / 3 * vol
        cum_vol += vol
        result.append(cum_pv / cum_vol if cum_vol > 0 else None)
    return result
