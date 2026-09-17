"""Structured logging (spec §67): one JSON object per line on stdout so
journald / `journalctl -o cat | jq` can filter by signal_id, setup_id,
underlying, decision or reason_code without regex on prose.

Keys are stable and sorted; `ts` is IST ISO-8601. Values that are not JSON
native fall back to str() rather than raising - a logging call must never
take the loop down.
"""
import datetime as dt
import json
import sys
from typing import IO

from trading_bot.engine.context import _json_default
from trading_bot.timeutil import now_ist

_stream: IO[str] = sys.stdout


def set_stream(stream: IO[str]) -> None:
    """Redirect output (tests, or a file sink in RESEARCH mode)."""
    global _stream
    _stream = stream


def event(
    component: str,
    event: str,
    *,
    severity: str = "INFO",
    signal_id: str | None = None,
    setup_id: str | None = None,
    underlying: str | None = None,
    decision: str | None = None,
    reason_code: str | None = None,
    ts: dt.datetime | None = None,
    **values,
) -> dict:
    rec = {
        "ts": (ts or now_ist()).isoformat(),
        "component": component,
        "event": event,
        "severity": severity,
    }
    for k, v in (
        ("signal_id", signal_id), ("setup_id", setup_id), ("underlying", underlying),
        ("decision", decision), ("reason_code", reason_code),
    ):
        if v is not None:
            rec[k] = v
    rec.update(values)
    try:
        line = json.dumps(rec, sort_keys=True, separators=(",", ":"), default=_json_default)
    except Exception as exc:  # pragma: no cover - defensive
        line = json.dumps({"ts": rec["ts"], "component": component, "event": event,
                           "severity": "ERROR", "log_error": str(exc)})
    _stream.write(line + "\n")
    _stream.flush()
    return rec
