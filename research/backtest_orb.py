"""Backtest an Opening-Range-Breakout (ORB) directional signal against real
historical NIFTY/BANKNIFTY spot data pulled by fetch_historical.py.

IMPORTANT SCOPE NOTE - read before trusting any number this prints:
This is a SPOT-PRICE PROXY for Zerodha's ORB-on-OPTION-PREMIUM strategy, not
a reproduction of it. SmartAPI's scrip master only lists currently-live
(unexpired) contracts - empirically confirmed (see research/README.md) that
there is no way to obtain a symbolToken, and therefore no way to pull
historical getCandleData, for any option that has already expired. Real
historical option premium history is NOT obtainable through this API. So
this backtest tests only the DIRECTIONAL SIGNAL (does a 09:15-11:15 opening-
range breakout in the underlying predict continued directional movement by
15:15?) using the underlying's own spot returns as the payoff - it does NOT
model option premium, theta decay, IV changes, strike selection, bid-ask
spread, or brokerage/STT. A positive result here is a reason to invest in
capturing real premium data going forward (the live bot could start logging
its own option quotes daily); it is NOT evidence the actual option strategy
is profitable.

Methodology:
- Reference window: 09:15-11:15 IST daily high/low.
- Entry: first candle after 11:15 whose CLOSE breaks above the reference
  high (long) or below the reference low (short) - close-based, not
  intrabar-touch, per Zerodha's own methodology. Filled at the NEXT
  candle's OPEN (not the signal candle's own close) to avoid lookahead.
- Stop: the OTHER side of the opening range (long stop = range low, short
  stop = range high) - the standard ORB stop convention, not an arbitrary
  parameter.
- Exit: 15:15 IST close if neither the stop nor the day's last candle is
  hit first, matching the live bot's own EXIT_TIME.
- Only the first breakout of the day is taken (matches "one shot" daily
  intraday framing of the live bot).
"""
import csv
import datetime as dt
import statistics
import sys
from collections import defaultdict
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"

REF_START = dt.time(9, 15)
REF_END = dt.time(11, 15)
EXIT_TIME = dt.time(15, 15)


def load_candles(path: Path) -> dict[dt.date, list[dict]]:
    by_day: dict[dt.date, list[dict]] = defaultdict(list)
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ts = dt.datetime.fromisoformat(row["timestamp"])
            by_day[ts.date()].append({
                "ts": ts,
                "time": ts.time(),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            })
    for day in by_day:
        by_day[day].sort(key=lambda c: c["ts"])
    return by_day


def backtest_day(candles: list[dict]) -> dict | None:
    ref = [c for c in candles if REF_START <= c["time"] <= REF_END]
    if len(ref) < 100:  # expect ~121 one-minute candles in a full 09:15-11:15 window
        return None
    range_high = max(c["high"] for c in ref)
    range_low = min(c["low"] for c in ref)

    after_ref = [c for c in candles if c["time"] > REF_END]
    direction = None
    entry_idx = None
    for i, c in enumerate(after_ref):
        if c["close"] > range_high:
            direction = "long"
            entry_idx = i
            break
        if c["close"] < range_low:
            direction = "short"
            entry_idx = i
            break
    if direction is None or entry_idx + 1 >= len(after_ref):
        return None  # no breakout, or breakout on the last candle with nothing left to fill on

    entry_candle = after_ref[entry_idx + 1]  # fill on the NEXT candle's open, not the signal candle
    entry_price = entry_candle["open"]
    stop_price = range_low if direction == "long" else range_high

    exit_price = None
    exit_reason = "eod"
    for c in after_ref[entry_idx + 1:]:
        if direction == "long" and c["low"] <= stop_price:
            exit_price = stop_price
            exit_reason = "stop"
            break
        if direction == "short" and c["high"] >= stop_price:
            exit_price = stop_price
            exit_reason = "stop"
            break
        if c["time"] >= EXIT_TIME:
            exit_price = c["close"]
            exit_reason = "time"
            break
    if exit_price is None:
        exit_price = after_ref[-1]["close"]
        exit_reason = "last_candle"

    ret_pct = (exit_price - entry_price) / entry_price if direction == "long" else (entry_price - exit_price) / entry_price
    return {
        "direction": direction,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "range_high": range_high,
        "range_low": range_low,
        "range_width_pct": (range_high - range_low) / range_low,
        "return_pct": ret_pct,
    }


def summarize(trades: list[dict], symbol: str) -> None:
    if not trades:
        print(f"{symbol}: no trades")
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

    stop_hits = sum(1 for t in trades if t["exit_reason"] == "stop")
    time_hits = sum(1 for t in trades if t["exit_reason"] in ("time", "last_candle"))

    print(f"\n=== {symbol}: SPOT-PROXY ORB backtest ===")
    print(f"win rate: {len(wins) / len(trades):.1%}  ({len(wins)}W / {len(losses)}L)")
    print(f"avg win: {statistics.mean(wins):.3%}" if wins else "avg win: n/a")
    print(f"avg loss: {statistics.mean(losses):.3%}" if losses else "avg loss: n/a")
    print(f"mean return/trade: {mean_r:.3%}  stdev: {std_r:.3%}")
    print(f"cumulative return (compounded, no sizing/costs): {(equity - 1):.2%}")
    print(f"max drawdown (equity curve, no sizing/costs): {max_dd:.2%}")
    print(f"trade-count-scaled Sharpe-like ratio: {sharpe_like:.2f}")
    print(f"exits: stop-hit {stop_hits}, time/eod {time_hits}")
    print(f"long trades: {sum(1 for t in trades if t['direction']=='long')}, "
          f"short trades: {sum(1 for t in trades if t['direction']=='short')}")


def run(symbol: str) -> None:
    path = DATA_DIR / f"{symbol}_1min.csv"
    if not path.exists():
        print(f"missing {path} - run fetch_historical.py first", file=sys.stderr)
        return
    by_day = load_candles(path)
    ref_ok_days = 0
    trades = []
    for day in sorted(by_day):
        ref = [c for c in by_day[day] if REF_START <= c["time"] <= REF_END]
        if len(ref) >= 100:
            ref_ok_days += 1
        result = backtest_day(by_day[day])
        if result is None:
            continue
        result["date"] = day
        trades.append(result)
    print(f"{symbol}: {len(by_day)} days of data, {ref_ok_days} with a full 09:15-11:15 "
          f"reference window, {len(trades)} with a breakout trade taken")
    summarize(trades, symbol)


if __name__ == "__main__":
    run("NIFTY")
    run("BANKNIFTY")
