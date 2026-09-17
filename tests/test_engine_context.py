import datetime as dt
import json

from trading_bot.engine.context import ContextSnapshot
from trading_bot.timeutil import IST


def _ctx(**over) -> ContextSnapshot:
    base = dict(
        ts=dt.datetime(2026, 9, 16, 9, 20, tzinfo=IST), underlying="NIFTY", trigger_tf="5m",
        spot=25000.0, session_phase="open_drive", quality="OK", bar_index=1,
        indicators={"atr": 20.0, "rsi": 55.0}, levels={"nearest_above": {"name": "pdh", "price": 25010.0}},
        trends={"5m": {"label": "BULL", "score": 0.5}},
    )
    base.update(over)
    return ContextSnapshot(**base)


def test_to_json_round_trips_and_is_deterministic():
    c = _ctx()
    j1, j2 = c.to_json(), c.to_json()
    assert j1 == j2
    d = json.loads(j1)
    assert d["ts"] == "2026-09-16T09:20:00+05:30"
    assert d["indicators"]["atr"] == 20.0 and d["underlying"] == "NIFTY"


def test_accessors():
    c = _ctx()
    assert c.atr == 20.0
    assert c.nearest("above")["name"] == "pdh"
    assert c.nearest("below") is None
    assert c.trend("5m")["label"] == "BULL" and c.trend("1d") == {}


def test_json_default_handles_sets_and_dates():
    c = _ctx(structure={"keys": {"b", "a"}, "day": dt.date(2026, 9, 16)})
    d = json.loads(c.to_json())
    assert d["structure"]["keys"] == ["a", "b"] and d["structure"]["day"] == "2026-09-16"
