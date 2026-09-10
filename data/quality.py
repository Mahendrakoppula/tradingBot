"""Data Quality Engine (spec Section 6). Runs against raw OHLCV rows
BEFORE anything downstream (features, strategies, ML) is allowed to
consume them. Never silently repairs data - every issue is reported, and
the caller decides whether to proceed, per the spec's "do not pretend
the data exists / is clean if it isn't" principle.

Deliberately pure-Python (no pandas dependency) - this runs on the exact
list[dict] shape data/historical.py produces, before storage.py ever
converts anything to a DataFrame, so it stays trivially unit-testable
with plain fixtures.
"""
import datetime as dt
from dataclasses import dataclass, field

# A single-bar move beyond this fraction of the previous close is flagged
# as a suspicious outlier (not auto-rejected - real gap-up/gap-down days
# happen, e.g. a large overnight index move) so a human/later stage can
# decide, rather than this module silently dropping a legitimate bar.
DEFAULT_OUTLIER_MOVE_FRACTION = 0.20


@dataclass
class DataQualityIssue:
    severity: str  # "error" | "warning"
    code: str
    message: str
    row_index: int | None = None


@dataclass
class DataQualityReport:
    symbol: str
    interval: str
    total_rows: int
    issues: list[DataQualityIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[DataQualityIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[DataQualityIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def is_clean(self) -> bool:
        """No ERRORS - warnings (e.g. an outlier move) don't block use,
        they just get surfaced."""
        return len(self.errors) == 0


def _parse_ts(value) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        return value
    try:
        return dt.datetime.fromisoformat(str(value))
    except ValueError:
        return None


def check_ohlcv(rows: list[dict], symbol: str, interval: str,
                 outlier_move_fraction: float = DEFAULT_OUTLIER_MOVE_FRACTION) -> DataQualityReport:
    report = DataQualityReport(symbol=symbol, interval=interval, total_rows=len(rows))

    if not rows:
        report.issues.append(DataQualityIssue("error", "empty", "No rows returned"))
        return report

    seen_timestamps: dict[dt.datetime, int] = {}
    prev_ts: dt.datetime | None = None
    prev_close: float | None = None

    for idx, row in enumerate(rows):
        ts = _parse_ts(row.get("timestamp"))
        if ts is None:
            report.issues.append(DataQualityIssue("error", "bad_timestamp", f"Unparseable timestamp: {row.get('timestamp')!r}", idx))
            continue

        if ts in seen_timestamps:
            report.issues.append(DataQualityIssue("error", "duplicate_timestamp", f"Duplicate timestamp {ts} (first seen at row {seen_timestamps[ts]})", idx))
        seen_timestamps[ts] = idx

        if prev_ts is not None and ts <= prev_ts:
            report.issues.append(DataQualityIssue("error", "non_monotonic", f"Timestamp {ts} does not come after previous {prev_ts}", idx))
        prev_ts = ts

        o, h, l, c, v = row.get("open"), row.get("high"), row.get("low"), row.get("close"), row.get("volume")
        if None in (o, h, l, c, v):
            report.issues.append(DataQualityIssue("error", "missing_field", f"Missing OHLCV field(s): {row}", idx))
            continue

        if o <= 0 or h <= 0 or l <= 0 or c <= 0:
            report.issues.append(DataQualityIssue("error", "non_positive_price", f"Non-positive price in {row}", idx))
        if v < 0:
            report.issues.append(DataQualityIssue("error", "negative_volume", f"Negative volume {v}", idx))
        if l > h:
            report.issues.append(DataQualityIssue("error", "low_above_high", f"low ({l}) > high ({h})", idx))
        if not (l <= o <= h):
            report.issues.append(DataQualityIssue("error", "open_out_of_range", f"open ({o}) not within [low {l}, high {h}]", idx))
        if not (l <= c <= h):
            report.issues.append(DataQualityIssue("error", "close_out_of_range", f"close ({c}) not within [low {l}, high {h}]", idx))

        if prev_close is not None and prev_close > 0:
            move = abs(c - prev_close) / prev_close
            if move > outlier_move_fraction:
                report.issues.append(DataQualityIssue(
                    "warning", "outlier_move",
                    f"Close moved {move:.1%} from previous close ({prev_close} -> {c}) - verify this is a real market move, not bad data",
                    idx,
                ))
        prev_close = c

    return report
