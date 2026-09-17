import datetime as dt
import io
import json

from trading_bot.engine import jsonlog
from trading_bot.timeutil import IST


def _capture():
    buf = io.StringIO()
    jsonlog.set_stream(buf)
    return buf


def test_one_json_line_with_sorted_keys():
    buf = _capture()
    ts = dt.datetime(2026, 9, 16, 9, 20, tzinfo=IST)
    rec = jsonlog.event("presignal", "stage_change", underlying="NIFTY", setup_id="s-1",
                        decision="watch", reason_code="evidence_threshold", ts=ts, confidence=0.3)
    out = buf.getvalue()
    assert out.count("\n") == 1 and out.endswith("\n")
    d = json.loads(out)
    assert d == rec
    assert d["ts"] == "2026-09-16T09:20:00+05:30" and d["severity"] == "INFO"
    assert d["component"] == "presignal" and d["confidence"] == 0.3
    assert list(d) == sorted(d)


def test_optional_ids_omitted_when_none():
    buf = _capture()
    jsonlog.event("feed", "connected", ts=dt.datetime(2026, 9, 16, 8, 0, tzinfo=IST))
    d = json.loads(buf.getvalue())
    assert "signal_id" not in d and "setup_id" not in d and "underlying" not in d


def test_non_native_values_are_stringified_not_raised():
    buf = _capture()
    jsonlog.event("x", "y", ts=dt.datetime(2026, 9, 16, 8, 0, tzinfo=IST),
                  when=dt.date(2026, 9, 16), keys={"b", "a"}, obj=object())
    d = json.loads(buf.getvalue())
    assert d["when"] == "2026-09-16" and d["keys"] == ["a", "b"] and d["obj"].startswith("<object")


def test_default_ts_is_ist():
    buf = _capture()
    jsonlog.event("x", "y", severity="WARN")
    d = json.loads(buf.getvalue())
    assert d["ts"].endswith("+05:30") and d["severity"] == "WARN"
