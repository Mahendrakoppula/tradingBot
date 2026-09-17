"""ContextSnapshot: everything the engines know about one underlying at one
moment, as plain JSON-serialisable data (spec §59 context fields, §64
context_snapshots table). Built once per trigger (5m close in M1), then
read by the pre-signal tracker, the explanation builder and the DAL.

Deliberately a flat bag of primitives/dicts rather than nested engine
dataclasses: it has to round-trip through JSON (Postgres jsonb, journald)
byte-identically for the §51 replay-parity test, and consumers should not
need to import every engine module to read it.
"""
import dataclasses
import datetime as dt
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ContextSnapshot:
    ts: dt.datetime
    underlying: str
    trigger_tf: str
    spot: float
    session_phase: str
    quality: str  # DataQuality.status - OK/STALE/GAP/INVALID/DISCONNECTED
    bar_index: int  # index of the trigger bar within the trigger_tf store

    # per-timeframe trend reads, keyed "1d"/"30m"/"5m"/"1m" -> asdict(TFTrend)
    trends: dict[str, dict] = field(default_factory=dict)
    alignment: dict = field(default_factory=dict)  # asdict(Alignment)
    regime: dict = field(default_factory=dict)  # asdict(MarketRegime)

    # structure: last_event kind/index/price, mss, swing counts, failed_breakout flags
    structure: dict = field(default_factory=dict)
    # named levels and distances: pdh/pdl/pwh/pwl/or_high/or_low/session_high/low,
    # nearest_above/nearest_below {name, price, distance, distance_atr, touches}
    levels: dict = field(default_factory=dict)
    # price action at the trigger bar: labels list + anatomy dict + sweep (if any)
    price_action: dict = field(default_factory=dict)
    # rsi, macd_hist, adx, atr, atr_pct, bb_width_pct, ema20/50/200, vwap
    indicators: dict = field(default_factory=dict)
    # relative_volume, volume_proxy ("futures"/"none"), obv_slope
    volume: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["ts"] = self.ts.isoformat()
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), default=_json_default)

    # --- small accessors the pre-signal tracker relies on -------------------

    @property
    def atr(self) -> float | None:
        return self.indicators.get("atr")

    def nearest(self, side: str) -> dict | None:
        """side: "above" | "below" -> {name, price, distance, distance_atr, touches} or None."""
        return self.levels.get(f"nearest_{side}")

    def trend(self, tf: str) -> dict:
        return self.trends.get(tf, {})


def _json_default(o: Any):
    if isinstance(o, (dt.datetime, dt.date, dt.time)):
        return o.isoformat()
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    if dataclasses.is_dataclass(o):
        return dataclasses.asdict(o)
    return str(o)
