"""Multi-timeframe (MTF) context: derives higher-timeframe (HTF) views from
already-cached lower-timeframe (LTF) data (see research/fetch_historical.py)
- no new data pulled here - and aligns each LTF candle with the most recent
HTF candle that had ALREADY CLOSED as of that LTF candle's own timestamp.

Getting this alignment wrong (using an HTF bar that hasn't closed yet, or
one that closes at/after the LTF bar being evaluated) is the easiest way to
leak future information into a backtest, so `aligned_view` is deliberately
conservative: an HTF bar only counts as "closed" once a LATER HTF bar has
started (its own close time isn't stored, but the next bar starting is
proof the previous one is done) - the most recent HTF bar in the series is
therefore NEVER treated as closed, since there's no later bar to prove it.

Candle dicts follow research/backtest_technical.py's
`load_candles_with_volume` shape (ts/date/time/open/high/low/close/volume),
sorted ascending by "ts" - both loaders in this repo already sort that way.

Reuses research.backtest_technical.resample (1-min -> N-min, same-day-only
bucketing, already used by the scalp/intraday backtests) for the LTF minute
case; adds resample_daily here for the HTF weekly/monthly case, which is a
day-based bucket boundary, not a time-of-day one, and resample doesn't
cover it.
"""
import datetime as dt
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from research.backtest_technical import resample as _resample_minutes  # noqa: E402


def resample_to_minutes(candles: list[dict], minutes: int) -> list[dict]:
    """5-min/15-min-from-1-min resampling for the LTF side - one name here
    so callers only need to import from research.framework.mtf."""
    return _resample_minutes(candles, minutes)


def resample_daily(candles: list[dict], unit: str) -> list[dict]:
    """Groups daily candles into weekly (ISO week, Mon-start) or monthly
    bars. `unit` is "week" or "month"."""
    if unit not in ("week", "month"):
        raise ValueError(f"unit must be 'week' or 'month', got {unit!r}")

    def _bucket_key(d: dt.date):
        if unit == "week":
            iso = d.isocalendar()
            return (iso[0], iso[1])
        return (d.year, d.month)

    out = []
    bucket: list[dict] = []
    bucket_key = None
    for c in candles:
        key = _bucket_key(c["date"])
        if bucket and key != bucket_key:
            out.append(_bucket_to_candle(bucket))
            bucket = []
        bucket_key = key
        bucket.append(c)
    if bucket:
        out.append(_bucket_to_candle(bucket))
    return out


def _bucket_to_candle(bucket: list[dict]) -> dict:
    return {
        "ts": bucket[0]["ts"],
        "date": bucket[0]["date"],
        "open": bucket[0]["open"],
        "high": max(c["high"] for c in bucket),
        "low": min(c["low"] for c in bucket),
        "close": bucket[-1]["close"],
        "volume": sum(c.get("volume", 0) or 0 for c in bucket),
    }


@dataclass
class MTFContext:
    ltf_index: int
    htf_index: int | None  # index into htf_candles of the last CLOSED bar, or None if none yet
    htf_candle: dict | None


def aligned_view(htf_candles: list[dict], ltf_candles: list[dict]) -> list[MTFContext]:
    """For every LTF candle (in order), finds the latest HTF candle proven
    closed as of that LTF candle's timestamp.

    HTF candle m is proven closed once htf_candles[m + 1] has itself
    started (consecutive, non-overlapping periods guarantee bar m ended by
    then) - so advancing the confirmed index from m to m+1 requires
    checking htf_candles[m + 2]["ts"] (the bar AFTER the new candidate),
    not htf_candles[m + 1]["ts"] (the candidate's own start, which would
    wrongly treat a bar as closed the moment it opens). The final HTF
    candle is never selected, since there's no later bar to prove its
    closure."""
    result: list[MTFContext] = []
    htf_i = -1  # last confirmed-closed index, -1 = none yet
    n_htf = len(htf_candles)
    for ltf_i, ltf_c in enumerate(ltf_candles):
        while htf_i + 2 < n_htf and htf_candles[htf_i + 2]["ts"] < ltf_c["ts"]:
            htf_i += 1
        if htf_i == -1:
            result.append(MTFContext(ltf_index=ltf_i, htf_index=None, htf_candle=None))
        else:
            result.append(MTFContext(ltf_index=ltf_i, htf_index=htf_i, htf_candle=htf_candles[htf_i]))
    return result
