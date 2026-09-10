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
from trading_bot.indicators import atr, ema, rolling_avg_volume, rsi, sma, vwap
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


def atr_stop_target(
    candles: list[dict], direction: str, entry_price: float, period: int, stop_mult: float, target_mult: float,
    *, baseline_period_mult: int = 3, trend_fast_period: int = 9, trend_slow_period: int = 21,
    stop_scale_min: float = 0.7, stop_scale_max: float = 1.5,
    target_scale_min: float = 1.0, target_scale_max: float = 1.8,
) -> tuple[float, float, float] | None:
    """ATR-based stop/target prices for `entry_price` - quoted in whatever
    price series `candles` is (underlying spot for the options tiers, the
    stock's own price for the swing equity leg), same convention used
    throughout this module for the percentage-based checks this replaces.
    Returns (stop_price, target_price, atr_value), or None if there isn't
    yet enough history for a `period`-length ATR - `atr_value` is always
    the raw short-period one (used later for trailing), not scaled by
    either adjustment below.

    Both `stop_mult`/`target_mult` are themselves scenario-adjusted, not
    applied as flat constants:
    - STOP side, by volatility regime: current ATR(`period`) vs its own
      longer-term baseline ATR(`period` x `baseline_period_mult`). Expanding
      volatility (ratio > 1) widens the stop so ordinary chop doesn't clip
      it; contracting volatility (ratio < 1) tightens it. Clamped to
      [`stop_scale_min`, `stop_scale_max`] so one outlier bar can't blow the
      stop out to something absurd.
    - TARGET side, by trend strength: EMA(`trend_fast_period`) vs
      EMA(`trend_slow_period`) spread, as a % of `entry_price`. A wide
      spread (strong trend) widens the target to let a winner run further;
      a near-zero spread (flat/choppy) keeps it at the base multiplier.
      Clamped to [`target_scale_min`, `target_scale_max`].
    Both are first-cut, unbacktested scaling choices - same caveat class as
    the base multipliers themselves (see module docstring)."""
    atr_series = atr(candles, period)
    atr_value = atr_series[-1]
    if atr_value is None or atr_value <= 0:
        return None

    effective_stop_mult = stop_mult
    baseline_atr = atr(candles, period * baseline_period_mult)[-1]
    if baseline_atr is not None and baseline_atr > 0:
        volatility_scale = min(max(atr_value / baseline_atr, stop_scale_min), stop_scale_max)
        effective_stop_mult = stop_mult * volatility_scale

    effective_target_mult = target_mult
    closes = [c["close"] for c in candles]
    fast_ma = ema(closes, trend_fast_period)[-1]
    slow_ma = ema(closes, trend_slow_period)[-1]
    if fast_ma is not None and slow_ma is not None and entry_price > 0:
        spread_pct = abs(fast_ma - slow_ma) / entry_price * 100
        trend_scale = min(max(1.0 + spread_pct, target_scale_min), target_scale_max)
        effective_target_mult = target_mult * trend_scale

    if direction == "long":
        return entry_price - effective_stop_mult * atr_value, entry_price + effective_target_mult * atr_value, atr_value
    return entry_price + effective_stop_mult * atr_value, entry_price - effective_target_mult * atr_value, atr_value


def should_activate_trailing(direction: str, entry_price: float, current_price: float, atr_value: float, activate_mult: float) -> bool:
    """True once price has moved favorably by >= activate_mult x ATR from
    entry - trailing only starts once a trade is already comfortably in
    profit, not from the very first tick in its favor."""
    if direction == "long":
        return current_price >= entry_price + activate_mult * atr_value
    return current_price <= entry_price - activate_mult * atr_value


def trailing_stop_price(direction: str, favorable_extreme: float, atr_value: float, trail_mult: float) -> float:
    """New stop level once trailing is active: `trail_mult` x ATR behind the
    best price seen since entry (`favorable_extreme` - highest for long,
    lowest for short). Callers must only adopt this if it's more favorable
    (tighter) than the current stop - a trailing stop must never loosen."""
    if direction == "long":
        return favorable_extreme - trail_mult * atr_value
    return favorable_extreme + trail_mult * atr_value


def premium_stop_target(entry_premium: float, lotsize: int, stop_rupees_per_lot: float, target_rupees_per_lot: float) -> tuple[float, float]:
    """Fixed rupee-per-lot stop/target, converted to a premium level - used
    by the scalp/intraday tiers INSTEAD of atr_stop_target(). Every position
    here is a BOUGHT option (CE or PE), so P&L is always
    (exit_premium - entry_premium) x quantity regardless of option type -
    there's no CE/PE direction to account for here, unlike the underlying-
    price convention atr_stop_target() uses. Callers should treat the
    result as direction "long" when calling stop_breached()/target_reached()
    (the option's own premium, not the underlying's price).

    A fixed rupee floor makes more sense here than a purely ATR-scaled one:
    live-verified 2026-09-10, several scalp trades closed with a gross
    P&L of only Rs.20-50/lot on a calm day - real round-trip transaction
    costs (see costs.py) can plausibly wipe out or reverse a gain that
    small, so a "win" needs to clear a real minimum before it's worth
    calling one."""
    return entry_premium - stop_rupees_per_lot / lotsize, entry_premium + target_rupees_per_lot / lotsize


def stop_breached(direction: str, stop_price: float, current_price: float) -> bool:
    return current_price <= stop_price if direction == "long" else current_price >= stop_price


def target_reached(direction: str, target_price: float, current_price: float) -> bool:
    return current_price >= target_price if direction == "long" else current_price <= target_price


def update_trailing_stop(
    direction: str, entry_price: float, current_price: float, atr_value: float,
    favorable_extreme: float, stop_price: float, trailing_active: bool,
    activate_mult: float, trail_mult: float,
) -> tuple[float, float, bool]:
    """One trailing-stop update step, called on every price check. Ratchets
    `favorable_extreme` in the trade's favor, activates trailing once price
    is `activate_mult` x ATR in profit from entry, and once active only ever
    TIGHTENS the stop toward `favorable_extreme` - never loosens it, and
    never overrides an existing stop that's already tighter than what
    trailing would compute right now. Returns (new_favorable_extreme,
    new_stop_price, new_trailing_active)."""
    if direction == "long":
        favorable_extreme = max(favorable_extreme, current_price)
    else:
        favorable_extreme = min(favorable_extreme, current_price)

    if not trailing_active and should_activate_trailing(direction, entry_price, current_price, atr_value, activate_mult):
        trailing_active = True

    if trailing_active:
        candidate = trailing_stop_price(direction, favorable_extreme, atr_value, trail_mult)
        stop_price = max(stop_price, candidate) if direction == "long" else min(stop_price, candidate)

    return favorable_extreme, stop_price, trailing_active


def swing_should_exit(direction: str, broken_level: float, latest_close: float, reclaim_buffer_pct: float) -> bool:
    """Trend-reversal exit: true once price closes back through the level it
    broke out from, by more than a buffer - see backtest_technical.py's
    SWING_LEVEL_RECLAIM_BUFFER_PCT comment for why a buffer is needed
    (without one, a normal post-breakout retest triggered almost every exit
    within a day or two)."""
    if direction == "long":
        return latest_close < broken_level * (1 - reclaim_buffer_pct / 100)
    return latest_close > broken_level * (1 + reclaim_buffer_pct / 100)


def relative_strength_confirmed(direction: str, own_closes: list[float], benchmark_closes: list[float], lookback: int) -> bool:
    """Cross-sectional relative-strength gate, adapted from
    research/framework/relative_strength.py's validated idea (walk-forward
    tested 2026-09-10 on an unbiased random F&O stock sample: gating
    breakout entries on this condition nearly quadrupled expectancy versus
    no gate) to this bot's own intraday bar cadence, rather than that
    module's daily bars.

    Requires the instrument's own return over the last `lookback` bars to
    be stronger than the benchmark's return over the same window for a
    bullish ("CE") signal, or weaker for a bearish ("PE") one - only take
    a directional signal on an instrument that's genuinely LEADING the
    market in that direction, not just moving with it.

    Fails closed (returns False) if either series doesn't have enough
    history yet - a missing benchmark reading should never read as
    "confirmed"."""
    if len(own_closes) <= lookback or len(benchmark_closes) <= lookback:
        return False
    own_now, own_then = own_closes[-1], own_closes[-1 - lookback]
    bench_now, bench_then = benchmark_closes[-1], benchmark_closes[-1 - lookback]
    if own_then == 0 or bench_then == 0:
        return False
    excess = (own_now - own_then) / own_then - (bench_now - bench_then) / bench_then
    if direction == "CE":
        return excess > 0
    if direction == "PE":
        return excess < 0
    raise ValueError(f"direction must be 'CE' or 'PE', got {direction!r}")
