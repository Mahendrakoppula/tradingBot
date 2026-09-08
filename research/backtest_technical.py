"""Backtest the technical-indicator strategy's three tiers (scalp/intraday/
swing) against cached CSV data from fetch_historical.py.

SAME SCOPE CAVEAT AS research/backtest_orb.py - read before trusting any
number this prints: this is a SPOT-PRICE PROXY (or, for the swing tier,
an EQUITY-PRICE proxy for the stock leg), not a reproduction of live
option/equity economics. It does NOT model premium, theta decay, IV,
strike selection, bid-ask spread, brokerage, or STT for the options tiers,
and does NOT model brokerage/STT/slippage for the equity swing tier either.
A positive result here is a reason to move to Phase 2 (live wiring,
dry-run); it is NOT evidence the real strategy is profitable.

Reuses indicators.py/support_resistance.py/chart_patterns.py/
volume_analysis.py (Phase 1, part 1) for all signal math - this file only
adds day-scanning/trade-simulation logic, mirroring backtest_orb.py's
`backtest_day()` -> `summarize()` shape (the per-day signal logic is new
per strategy; the CSV-loading and win-rate/Sharpe-like/drawdown metrics
pattern is deliberately copied, not reinvented).

Every per-tier signal combination below (which periods, which filters are
required together) is a FIRST-CUT, UNBACKTESTED ASSEMBLY - this script's
whole purpose is to put a real number on that before Phase 2 (live wiring)
begins, per .claude/plans/goofy-plotting-sedgewick.md.
"""
import csv
import datetime as dt
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trading_bot.chart_patterns import detect_ma_crossover, detect_trend_structure
from trading_bot.indicators import ema, macd, rolling_avg_volume, rsi, sma, vwap
from trading_bot.support_resistance import classic_pivot_points, find_swing_points
from trading_bot.volume_analysis import volume_confirms_move

DATA_DIR = Path(__file__).resolve().parent / "data"

EXIT_TIME = dt.time(15, 15)  # matches the live bot's own EXIT_TIME

# --- Scalp tier config (first-cut, see module docstring) ---
SCALP_EMA_FAST, SCALP_EMA_SLOW = 9, 21
SCALP_AVG_VOLUME_PERIOD = 20
SCALP_MIN_RELATIVE_VOLUME = 1.5
SCALP_MAX_HOLD_BARS = 15  # 1-min bars - widened from 5 (config.py's SCALP_MAX_HOLD_MINUTES
                           # default): backtest showed the stop/target almost NEVER fired within
                           # 5 minutes (e.g. NIFTY: 182 "time" exits, 1 "stop", 0 "take_profit" -
                           # nearly every trade just drifted to the timer), meaning 5 min doesn't
                           # give the EMA9/21 signal enough room to actually develop before being
                           # judged. Live SCALP_MAX_HOLD_MINUTES stays 5 until this is validated -
                           # this is backtest-only tuning, not yet a recommendation for live config.
SCALP_STOP_PCT = 0.5  # % adverse move - widened from 0.3 (~1.7x, matching sqrt(15/5) volatility scaling)
SCALP_TAKE_PROFIT_PCT = 0.85  # ~1.7:1 reward:risk vs the stop, same ratio as before

# --- Intraday tier config (first-cut) ---
INTRADAY_BAR_MINUTES = 5
INTRADAY_EMA_FAST, INTRADAY_EMA_SLOW = 20, 50
INTRADAY_RSI_PERIOD = 14
INTRADAY_AVG_VOLUME_PERIOD = 20
INTRADAY_MIN_RELATIVE_VOLUME = 1.5
INTRADAY_STOP_PCT = 1.0
INTRADAY_TAKE_PROFIT_PCT = 1.7  # ~1.7:1 reward:risk - v1 had NO take-profit, only a stop and the
                                 # same-day EXIT_TIME close, so a profitable move could round-trip
                                 # back down by 15:15 before being captured (confirmed: negative
                                 # avg expectancy across most symbols in the first backtest run)

# --- Swing tier config (first-cut) ---
SWING_SMA_FAST, SWING_SMA_SLOW = 50, 200
SWING_RSI_PERIOD = 14
SWING_SWING_LEFT_RIGHT = 5  # fractal window on daily bars
SWING_STOP_PCT = 8.0  # risk backstop, on top of the trend-reversal exit
SWING_MAX_HOLD_DAYS = 90  # sanity ceiling so a trade can't run "forever" in the backtest


# --- shared CSV loading (candle dicts include volume, unlike backtest_orb.py's) ---


def load_candles_with_volume(path: Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ts = dt.datetime.fromisoformat(row["timestamp"])
            rows.append({
                "ts": ts, "date": ts.date(), "time": ts.time(),
                "open": float(row["open"]), "high": float(row["high"]),
                "low": float(row["low"]), "close": float(row["close"]),
                "volume": float(row["volume"] or 0),
            })
    rows.sort(key=lambda c: c["ts"])
    return rows


def group_by_day(candles: list[dict]) -> dict[dt.date, list[dict]]:
    by_day: dict[dt.date, list[dict]] = defaultdict(list)
    for c in candles:
        by_day[c["date"]].append(c)
    return dict(by_day)


def resample(candles: list[dict], minutes: int) -> list[dict]:
    """Groups consecutive 1-minute candles (same day only - a bucket never
    spans a day boundary) into `minutes`-wide OHLCV bars."""
    out = []
    bucket: list[dict] = []
    bucket_start = None
    for c in candles:
        slot = c["ts"].replace(
            minute=(c["ts"].minute // minutes) * minutes, second=0, microsecond=0
        )
        if bucket and (slot != bucket_start or c["date"] != bucket[0]["date"]):
            out.append(_bucket_to_candle(bucket))
            bucket = []
        bucket_start = slot
        bucket.append(c)
    if bucket:
        out.append(_bucket_to_candle(bucket))
    return out


def _bucket_to_candle(bucket: list[dict]) -> dict:
    return {
        "ts": bucket[0]["ts"], "date": bucket[0]["date"], "time": bucket[0]["time"],
        "open": bucket[0]["open"], "high": max(c["high"] for c in bucket),
        "low": min(c["low"] for c in bucket), "close": bucket[-1]["close"],
        "volume": sum(c["volume"] for c in bucket),
    }


# --- generic trade summary (adapted from backtest_orb.summarize - exit
# reasons differ per tier here, so this reports a generic breakdown instead
# of backtest_orb's hardcoded stop/time buckets) ---


def summarize(trades: list[dict], label: str) -> None:
    if not trades:
        print(f"{label}: no trades")
        return
    returns = [t["return_pct"] for t in trades]
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]

    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in returns:
        equity *= (1 + r)
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)

    mean_r = statistics.mean(returns)
    std_r = statistics.pstdev(returns) if len(returns) > 1 else 0.0
    sharpe_like = (mean_r / std_r) * (len(returns) ** 0.5) if std_r > 0 else float("nan")

    exit_counts: dict[str, int] = defaultdict(int)
    for t in trades:
        exit_counts[t["exit_reason"]] += 1

    print(f"\n=== {label}: {len(trades)} trades ===")
    print(f"win rate: {len(wins) / len(trades):.1%}  ({len(wins)}W / {len(losses)}L)")
    print(f"avg win: {statistics.mean(wins):.3%}" if wins else "avg win: n/a")
    print(f"avg loss: {statistics.mean(losses):.3%}" if losses else "avg loss: n/a")
    print(f"mean return/trade: {mean_r:.3%}  stdev: {std_r:.3%}")
    print(f"cumulative return (compounded, no sizing/costs): {(equity - 1):.2%}")
    print(f"max drawdown (equity curve, no sizing/costs): {max_dd:.2%}")
    print(f"trade-count-scaled Sharpe-like ratio: {sharpe_like:.2f}")
    print(f"exit reasons: {dict(exit_counts)}")
    print(f"long trades: {sum(1 for t in trades if t['direction']=='long')}, "
          f"short trades: {sum(1 for t in trades if t['direction']=='short')}")


# --- Scalp tier: 1-min candles, single day, first signal only ---


def backtest_scalp_day(candles: list[dict]) -> dict | None:
    n = len(candles)
    warmup = max(SCALP_EMA_SLOW, SCALP_AVG_VOLUME_PERIOD) + 1
    if n < warmup + SCALP_MAX_HOLD_BARS + 2:
        return None
    # Index candles (NIFTY/BANKNIFTY) always report volume=0 - confirmed
    # live, there's no "shares traded" for an index value itself, only for
    # its constituents/derivatives. Requiring volume confirmation would
    # silently block every signal for indices, so skip that gate entirely
    # when the data has no volume information at all (not when a bar's
    # volume is legitimately low - that's still a real no-confirmation).
    has_volume_data = any((c.get("volume") or 0) > 0 for c in candles)
    closes = [c["close"] for c in candles]
    ema_fast = ema(closes, SCALP_EMA_FAST)
    ema_slow = ema(closes, SCALP_EMA_SLOW)
    vwap_series = vwap(candles)
    avg_vol = rolling_avg_volume(candles, SCALP_AVG_VOLUME_PERIOD)

    direction = None
    signal_idx = None
    for i in range(warmup, n - 1):
        cross = detect_ma_crossover(ema_fast, ema_slow, index=i)
        if cross is None or avg_vol[i] is None:
            continue
        if has_volume_data and not volume_confirms_move(candles[i], avg_vol[i], SCALP_MIN_RELATIVE_VOLUME):
            continue
        # vwap() falls back to close when cumulative volume is 0 (confirmed
        # live: NIFTY/BANKNIFTY candles always report volume=0, there's no
        # real traded volume for an index value itself) - so vwap[i] ==
        # close[i] on EVERY bar for these, and the "close vs vwap" side
        # filter below can never pass (it's a strict inequality against
        # itself). Skip that side of the filter when there's no real volume
        # to compute a real VWAP from - this tests the EMA-cross signal
        # alone for indices, not EMA-cross + VWAP-confirmed, a real
        # difference in what's being validated worth revisiting before
        # live wiring (e.g. substitute a session SMA as the reference line).
        if not has_volume_data:
            if cross == "golden_cross":
                direction, signal_idx = "long", i
                break
            if cross == "death_cross":
                direction, signal_idx = "short", i
                break
            continue
        if cross == "golden_cross" and closes[i] > vwap_series[i]:
            direction, signal_idx = "long", i
            break
        if cross == "death_cross" and closes[i] < vwap_series[i]:
            direction, signal_idx = "short", i
            break
    if direction is None or signal_idx + 1 >= n:
        return None

    entry_price = candles[signal_idx + 1]["open"]  # fill on the NEXT bar's open, avoid lookahead
    stop_price = entry_price * (1 - SCALP_STOP_PCT / 100) if direction == "long" else entry_price * (1 + SCALP_STOP_PCT / 100)
    take_profit_price = entry_price * (1 + SCALP_TAKE_PROFIT_PCT / 100) if direction == "long" else entry_price * (1 - SCALP_TAKE_PROFIT_PCT / 100)
    hold_end = min(signal_idx + 1 + SCALP_MAX_HOLD_BARS, n - 1)

    exit_price, exit_reason = None, "time"
    for c in candles[signal_idx + 2 : hold_end + 1]:
        if direction == "long":
            if c["low"] <= stop_price:
                exit_price, exit_reason = stop_price, "stop"
                break
            if c["high"] >= take_profit_price:
                exit_price, exit_reason = take_profit_price, "take_profit"
                break
        else:
            if c["high"] >= stop_price:
                exit_price, exit_reason = stop_price, "stop"
                break
            if c["low"] <= take_profit_price:
                exit_price, exit_reason = take_profit_price, "take_profit"
                break
    if exit_price is None:
        exit_price, exit_reason = candles[hold_end]["close"], "time"

    ret = (exit_price - entry_price) / entry_price if direction == "long" else (entry_price - exit_price) / entry_price
    return {"direction": direction, "entry_price": entry_price, "exit_price": exit_price,
            "exit_reason": exit_reason, "return_pct": ret}


# --- Intraday tier: 5-min resampled candles, continuous multi-day indicator
# series (so EMA/RSI have real history), entries/exits scoped to one day ---


def backtest_intraday(minute_candles: list[dict], daily_candles: list[dict]) -> list[dict]:
    bars = resample(minute_candles, INTRADAY_BAR_MINUTES)
    n = len(bars)
    if n < INTRADAY_EMA_SLOW + 5:
        return []
    has_volume_data = any((b.get("volume") or 0) > 0 for b in bars)  # see backtest_scalp_day's comment - indices report 0
    closes = [b["close"] for b in bars]
    ema_fast = ema(closes, INTRADAY_EMA_FAST)
    ema_slow = ema(closes, INTRADAY_EMA_SLOW)
    rsi_series = rsi(closes, INTRADAY_RSI_PERIOD)
    avg_vol = rolling_avg_volume(bars, INTRADAY_AVG_VOLUME_PERIOD)

    prev_day_ohlc = {}  # date -> (prev_high, prev_low, prev_close), for classic_pivot_points
    daily_by_date = {c["date"]: c for c in daily_candles}
    sorted_days = sorted(daily_by_date)
    for i, d in enumerate(sorted_days):
        if i > 0:
            prev = daily_by_date[sorted_days[i - 1]]
            prev_day_ohlc[d] = (prev["high"], prev["low"], prev["close"])

    trades = []
    warmup = INTRADAY_EMA_SLOW + 1
    day_of_signal = None
    i = warmup
    while i < n - 1:
        bar = bars[i]
        if bar["date"] == day_of_signal:
            i += 1
            continue  # only first signal per day
        if avg_vol[i] is None or ema_fast[i] is None or rsi_series[i] is None:
            i += 1
            continue
        pivots = classic_pivot_points(*prev_day_ohlc[bar["date"]]) if bar["date"] in prev_day_ohlc else None

        cross = detect_ma_crossover(ema_fast, ema_slow, index=i)
        breakout_up = pivots is not None and closes[i] > pivots["r1"]
        breakout_down = pivots is not None and closes[i] < pivots["s1"]

        direction = None
        if (cross == "golden_cross" or breakout_up) and rsi_series[i] < 70:
            direction = "long"
        elif (cross == "death_cross" or breakout_down) and rsi_series[i] > 30:
            direction = "short"
        if direction is None or (has_volume_data and not volume_confirms_move(bar, avg_vol[i], INTRADAY_MIN_RELATIVE_VOLUME)):
            i += 1
            continue

        entry_price = bars[i + 1]["open"]
        stop_price = entry_price * (1 - INTRADAY_STOP_PCT / 100) if direction == "long" else entry_price * (1 + INTRADAY_STOP_PCT / 100)
        take_profit_price = entry_price * (1 + INTRADAY_TAKE_PROFIT_PCT / 100) if direction == "long" else entry_price * (1 - INTRADAY_TAKE_PROFIT_PCT / 100)
        exit_price, exit_reason = None, "time"
        j = i + 2
        while j < n and bars[j]["date"] == bar["date"]:
            c = bars[j]
            if direction == "long":
                if c["low"] <= stop_price:
                    exit_price, exit_reason = stop_price, "stop"
                    break
                if c["high"] >= take_profit_price:
                    exit_price, exit_reason = take_profit_price, "take_profit"
                    break
            else:
                if c["high"] >= stop_price:
                    exit_price, exit_reason = stop_price, "stop"
                    break
                if c["low"] <= take_profit_price:
                    exit_price, exit_reason = take_profit_price, "take_profit"
                    break
            if c["time"] >= EXIT_TIME:
                exit_price, exit_reason = c["close"], "exit_time"
                break
            j += 1
        if exit_price is None:
            # ran off the end of the day's bars without an explicit exit -
            # close at the last bar of that day
            same_day = [b for b in bars[i + 1 : j] if b["date"] == bar["date"]]
            exit_price, exit_reason = (same_day[-1]["close"] if same_day else entry_price), "eod_fallback"

        ret = (exit_price - entry_price) / entry_price if direction == "long" else (entry_price - exit_price) / entry_price
        trades.append({"direction": direction, "entry_price": entry_price, "exit_price": exit_price,
                        "exit_reason": exit_reason, "return_pct": ret, "date": bar["date"]})
        day_of_signal = bar["date"]
        i += 1
    return trades


# --- Swing tier: daily candles, whole multi-year series, trend-reversal exit ---


def backtest_swing(daily_candles: list[dict]) -> list[dict]:
    n = len(daily_candles)
    warmup = SWING_SMA_SLOW + 1
    if n < warmup + 10:
        return []
    closes = [c["close"] for c in daily_candles]
    sma_fast = sma(closes, SWING_SMA_FAST)
    sma_slow = sma(closes, SWING_SMA_SLOW)
    rsi_series = rsi(closes, SWING_RSI_PERIOD)
    _, _, hist = macd(closes)
    # Computed ONCE over the whole series - safe because whether index j is a
    # swing point only depends on candles in [j-left, j+right], which is
    # already fixed data by the time j+right has occurred. Filtering by
    # `p.index + right <= i` below is what keeps each day's decision from
    # seeing swing points that weren't confirmed yet - no lookahead.
    all_points = find_swing_points(daily_candles, left=SWING_SWING_LEFT_RIGHT, right=SWING_SWING_LEFT_RIGHT)

    trades = []
    regime = None  # "uptrend" | "downtrend" | None, flips only on a golden/death cross
    position = None  # dict or None
    for i in range(warmup, n):
        cross = detect_ma_crossover(sma_fast, sma_slow, index=i)
        if cross == "golden_cross":
            regime = "uptrend"
        elif cross == "death_cross":
            regime = "downtrend"

        known_points = [p for p in all_points if p.index + SWING_SWING_LEFT_RIGHT <= i]
        structure = detect_trend_structure(known_points)

        if position is not None:
            days_held = i - position["entry_idx"]
            hist_prev, hist_now = hist[i - 1], hist[i]
            structure_flipped = (
                (position["direction"] == "long" and structure == "downtrend")
                or (position["direction"] == "short" and structure == "uptrend")
            )
            regime_flipped = (
                (position["direction"] == "long" and cross == "death_cross")
                or (position["direction"] == "short" and cross == "golden_cross")
            )
            stop_hit = (
                (position["direction"] == "long" and closes[i] <= position["stop_price"])
                or (position["direction"] == "short" and closes[i] >= position["stop_price"])
            )
            if structure_flipped or regime_flipped or stop_hit or days_held >= SWING_MAX_HOLD_DAYS:
                exit_price = closes[i]
                exit_reason = "stop" if stop_hit else ("trend_reversal" if (structure_flipped or regime_flipped) else "max_hold")
                ret = (
                    (exit_price - position["entry_price"]) / position["entry_price"]
                    if position["direction"] == "long"
                    else (position["entry_price"] - exit_price) / position["entry_price"]
                )
                trades.append({"direction": position["direction"], "entry_price": position["entry_price"],
                                "exit_price": exit_price, "exit_reason": exit_reason, "return_pct": ret,
                                "entry_date": daily_candles[position["entry_idx"]]["date"], "exit_date": daily_candles[i]["date"]})
                position = None
            continue  # one position at a time - no new entry the same day a position is open

        if i + 1 >= n or hist[i - 1] is None or hist[i] is None or rsi_series[i] is None:
            continue
        hist_prev, hist_now = hist[i - 1], hist[i]
        bullish_trigger = hist_prev <= 0 < hist_now
        bearish_trigger = hist_prev >= 0 > hist_now

        direction = None
        if regime == "uptrend" and structure == "uptrend" and bullish_trigger and rsi_series[i] < 70:
            direction = "long"
        elif regime == "downtrend" and structure == "downtrend" and bearish_trigger and rsi_series[i] > 30:
            direction = "short"
        if direction is None:
            continue

        entry_price = daily_candles[i + 1]["open"]  # next day's open, avoid lookahead
        stop_price = entry_price * (1 - SWING_STOP_PCT / 100) if direction == "long" else entry_price * (1 + SWING_STOP_PCT / 100)
        position = {"direction": direction, "entry_price": entry_price, "entry_idx": i + 1, "stop_price": stop_price}

    return trades


def run(symbol: str) -> None:
    minute_path = DATA_DIR / f"{symbol}_1min.csv"
    daily_path = DATA_DIR / f"{symbol}_1day.csv"
    if not daily_path.exists():
        print(f"missing {daily_path} - run fetch_historical.py first", file=sys.stderr)
        return

    daily_candles = load_candles_with_volume(daily_path)
    swing_trades = backtest_swing(daily_candles)
    summarize(swing_trades, f"{symbol} SWING (daily, trend-reversal exit)")

    if minute_path.exists():
        minute_candles = load_candles_with_volume(minute_path)
        by_day = group_by_day(minute_candles)

        scalp_trades = []
        for day in sorted(by_day):
            result = backtest_scalp_day(by_day[day])
            if result is not None:
                scalp_trades.append(result)
        summarize(scalp_trades, f"{symbol} SCALP (1-min, EMA9/21 x VWAP x volume)")

        intraday_trades = backtest_intraday(minute_candles, daily_candles)
        summarize(intraday_trades, f"{symbol} INTRADAY (5-min, EMA20/50 or pivot breakout)")
    else:
        print(f"{symbol}: no 1-min data ({minute_path} missing) - skipping scalp/intraday")


if __name__ == "__main__":
    symbols = sys.argv[1:] or ["NIFTY", "BANKNIFTY"]
    for sym in symbols:
        run(sym)
