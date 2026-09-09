"""Live signal functions for the technical-indicator bot's three tiers
(scalp/intraday/swing) - see .claude/plans/goofy-plotting-sedgewick.md for
the full design and research/backtest_technical.py for the backtest these
mirror.

Each function looks only at the LATEST bar in the candle list it's given
(index -1) - unlike the backtest, which scans a whole day/series for the
first signal, live has no such luxury: it's called once per new bar/day and
just asks "is there a fresh signal right now". Callers are responsible for
fetching fresh candles on the right cadence and for not re-entering a
position that's already open.

Same honesty caveat as backtest_technical.py's module docstring: period/
threshold choices here are the ones backtested there, not independently
re-validated for live spread/slippage/theta - see that module's docstring
and the Phase 1 backtest output reviewed with the user before this was
built.
"""
from trading_bot.chart_patterns import detect_breakout, detect_ma_crossover
from trading_bot.indicators import ema, rolling_avg_volume, rsi, sma, vwap
from trading_bot.support_resistance import cluster_levels, find_swing_points
from trading_bot.volume_analysis import volume_confirms_move


def candles_from_rows(rows: list) -> list[dict]:
    """Converts getCandleData's raw [timestamp, open, high, low, close,
    volume] rows into the candle-dict shape indicators.py/chart_patterns.py
    expect (same shape as candle_history_logger.fetch_candles_with_oi)."""
    return [
        {"time": row[0], "open": row[1], "high": row[2], "low": row[3], "close": row[4], "volume": row[5] or 0}
        for row in (rows or [])
    ]


def scalp_signal(
    candles: list[dict], ema_fast: int, ema_slow: int,
    avg_volume_period: int, min_relative_volume: float,
) -> tuple[str | None, str]:
    """EMA(fast)/EMA(slow) crossover on the latest 1-min bar, confirmed by
    VWAP side + relative volume - live equivalent of
    backtest_technical.backtest_scalp_day's signal. `candles` should be
    TODAY's 1-min bars so far, oldest first. Returns ("CE"/"PE"/None, reason).
    """
    n = len(candles)
    warmup = max(ema_slow, avg_volume_period) + 1
    if n < warmup + 1:
        return None, "not enough candles yet for warmup"

    # Index candles (NIFTY/BANKNIFTY) always report volume=0 - there's no
    # "shares traded" for an index value itself. Requiring volume
    # confirmation would silently block every signal for indices, so skip
    # that gate (and the VWAP side check, which degrades to "close vs
    # itself" when cumulative volume is 0 - see indicators.vwap) entirely
    # when the data has no volume information at all.
    has_volume_data = any((c.get("volume") or 0) > 0 for c in candles)
    closes = [c["close"] for c in candles]
    ema_f = ema(closes, ema_fast)
    ema_s = ema(closes, ema_slow)
    avg_vol = rolling_avg_volume(candles, avg_volume_period)
    i = n - 1

    cross = detect_ma_crossover(ema_f, ema_s, index=i)
    if cross is None:
        return None, "no EMA crossover on the latest bar"
    if avg_vol[i] is None:
        return None, "not enough history for average volume yet"

    if not has_volume_data:
        if cross == "golden_cross":
            return "CE", f"golden cross (EMA{ema_fast}/{ema_slow}), no volume data (index) - EMA-only signal"
        return "PE", f"death cross (EMA{ema_fast}/{ema_slow}), no volume data (index) - EMA-only signal"

    if not volume_confirms_move(candles[i], avg_vol[i], min_relative_volume):
        return None, "crossover not confirmed by relative volume"

    vwap_series = vwap(candles)
    if cross == "golden_cross" and closes[i] > vwap_series[i]:
        return "CE", f"golden cross (EMA{ema_fast}/{ema_slow}) above VWAP, volume confirmed"
    if cross == "death_cross" and closes[i] < vwap_series[i]:
        return "PE", f"death cross (EMA{ema_fast}/{ema_slow}) below VWAP, volume confirmed"
    return None, "crossover direction disagrees with VWAP side"


def intraday_signal(
    bars: list[dict], pivots: dict | None, ema_fast: int, ema_slow: int, rsi_period: int,
    avg_volume_period: int, min_relative_volume: float,
) -> tuple[str | None, str]:
    """EMA(fast)/EMA(slow) crossover OR a close-based breakout of the prior
    session's classic pivot R1/S1, gated by RSI not being extreme against
    the direction and confirmed by relative volume - live equivalent of
    backtest_technical.backtest_intraday's signal. `bars` should be TODAY's
    5-min bars so far, oldest first."""
    n = len(bars)
    warmup = ema_slow + 1
    if n < warmup + 1:
        return None, "not enough bars yet for warmup"

    has_volume_data = any((b.get("volume") or 0) > 0 for b in bars)
    closes = [b["close"] for b in bars]
    ema_f = ema(closes, ema_fast)
    ema_s = ema(closes, ema_slow)
    rsi_series = rsi(closes, rsi_period)
    avg_vol = rolling_avg_volume(bars, avg_volume_period)
    i = n - 1
    if avg_vol[i] is None or ema_f[i] is None or rsi_series[i] is None:
        return None, "not enough history yet"

    cross = detect_ma_crossover(ema_f, ema_s, index=i)
    breakout_up = pivots is not None and closes[i] > pivots["r1"]
    breakout_down = pivots is not None and closes[i] < pivots["s1"]

    direction, trigger = None, None
    if (cross == "golden_cross" or breakout_up) and rsi_series[i] < 70:
        direction = "CE"
        trigger = "EMA cross" if cross == "golden_cross" else "pivot R1 breakout"
    elif (cross == "death_cross" or breakout_down) and rsi_series[i] > 30:
        direction = "PE"
        trigger = "EMA cross" if cross == "death_cross" else "pivot S1 breakout"
    if direction is None:
        return None, "no EMA cross/pivot breakout, or RSI extreme blocked it"
    if has_volume_data and not volume_confirms_move(bars[i], avg_vol[i], min_relative_volume):
        return None, "signal not confirmed by relative volume"

    return direction, f"{trigger} (RSI {rsi_series[i]:.1f}), volume {'confirmed' if has_volume_data else 'n/a (index)'}"


def swing_signal(
    daily_candles: list[dict], sma_fast: int, sma_slow: int, rsi_period: int,
    swing_left_right: int, sr_tolerance_pct: float, sr_min_touches: int,
) -> tuple[str | None, float | None, str]:
    """Close-based breakout of a real multi-touch support/resistance level
    (cluster_levels), gated by RSI and the SMA(fast)/SMA(slow) regime - live
    equivalent of backtest_technical.backtest_swing's entry signal.
    `daily_candles` should be daily bars up to and including the most
    recently closed session, oldest first. Returns (direction, broken_level,
    reason) where direction is "long"/"short"/None - "short" is only
    actionable via an option (index); a stock swing position can't be
    shorted (no equity-shorting infra in this repo), callers must skip it.
    """
    n = len(daily_candles)
    warmup = sma_slow + 1
    if n < warmup + swing_left_right + 2:
        return None, None, "not enough daily history yet"

    closes = [c["close"] for c in daily_candles]
    sma_f = sma(closes, sma_fast)
    sma_s = sma(closes, sma_slow)
    rsi_series = rsi(closes, rsi_period)
    i = n - 1
    if rsi_series[i] is None:
        return None, None, "not enough history for RSI yet"

    # Regime = the most recent golden/death cross seen anywhere in history,
    # not just at i - same "sticky until it flips" semantics as the backtest.
    regime = None
    for j in range(warmup, i + 1):
        cross = detect_ma_crossover(sma_f, sma_s, index=j)
        if cross == "golden_cross":
            regime = "uptrend"
        elif cross == "death_cross":
            regime = "downtrend"

    all_points = find_swing_points(daily_candles, left=swing_left_right, right=swing_left_right)
    known_points = [p for p in all_points if p.index + swing_left_right <= i]
    levels = cluster_levels(known_points, sr_tolerance_pct)
    prev_close = closes[i - 1]
    resistances = [lv for lv in levels if lv.kind == "high" and lv.touches >= sr_min_touches and lv.price > prev_close]
    supports = [lv for lv in levels if lv.kind == "low" and lv.touches >= sr_min_touches and lv.price < prev_close]

    if resistances:
        nearest = min(resistances, key=lambda lv: lv.price)
        if detect_breakout(daily_candles, nearest.price, "up", index=i) and rsi_series[i] < 70 and regime != "downtrend":
            return "long", nearest.price, (
                f"closed above resistance {nearest.price:.2f} ({nearest.touches} touches), "
                f"RSI {rsi_series[i]:.1f}, regime {regime or 'neutral'}"
            )
    if supports:
        nearest = max(supports, key=lambda lv: lv.price)
        if detect_breakout(daily_candles, nearest.price, "down", index=i) and rsi_series[i] > 30 and regime != "uptrend":
            return "short", nearest.price, (
                f"closed below support {nearest.price:.2f} ({nearest.touches} touches), "
                f"RSI {rsi_series[i]:.1f}, regime {regime or 'neutral'}"
            )
    return None, None, "no valid S/R breakout as of the latest close"


def swing_should_exit(direction: str, broken_level: float, latest_close: float, reclaim_buffer_pct: float) -> bool:
    """Trend-reversal exit: true once price closes back through the level it
    broke out from, by more than a buffer - see backtest_technical.py's
    SWING_LEVEL_RECLAIM_BUFFER_PCT comment for why a buffer is needed
    (without one, a normal post-breakout retest triggered almost every exit
    within a day or two)."""
    if direction == "long":
        return latest_close < broken_level * (1 - reclaim_buffer_pct / 100)
    return latest_close > broken_level * (1 + reclaim_buffer_pct / 100)
