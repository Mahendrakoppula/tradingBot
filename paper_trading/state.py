"""Persisted state for the live paper-trading loop across daily script
invocations (a systemd timer runs this once per day - not a
continuously running process, matching the backtester's own
daily-bars-only scope). The DailyRiskEngine itself needs NO cross-day
persistence: backtesting/event_loop.py's process_bar() resets it
unconditionally on every call, which happens to align exactly with "one
call per trading day" - a fresh DailyRiskEngine() each script run is
already correctly reset. Only the open Trade (if any), the completed-
trades log, and the last-processed bar (for idempotency - see
paper_trading/daily_loop.py) need to survive between days.
"""
import dataclasses
import datetime as dt
import json
from pathlib import Path

from backtesting.trade_record import Trade

STATE_DIR = Path(__file__).resolve().parent.parent / ".state" / "paper_trading"

_DATE_FIELDS = ("expiry",)
_DATETIME_FIELDS = ("entry_timestamp", "exit_timestamp")


def _trade_to_dict(trade: Trade) -> dict:
    d = dataclasses.asdict(trade)
    for key in _DATE_FIELDS + _DATETIME_FIELDS:
        if d.get(key) is not None and hasattr(d[key], "isoformat"):
            d[key] = d[key].isoformat()
    return d


def _trade_from_dict(d: dict) -> Trade:
    d = dict(d)
    for key in _DATE_FIELDS:
        if d.get(key):
            d[key] = dt.date.fromisoformat(d[key])
    for key in _DATETIME_FIELDS:
        if d.get(key):
            d[key] = dt.datetime.fromisoformat(d[key])
    return Trade(**d)


@dataclasses.dataclass
class PaperTradingState:
    open_trade: Trade | None = None
    completed_trades: list[Trade] = dataclasses.field(default_factory=list)
    last_processed_timestamp: str | None = None  # isoformat of the last bar actually processed - see daily_loop.py


def _state_path(instrument: str, state_dir: Path) -> Path:
    return state_dir / f"{instrument}.json"


def load_state(instrument: str, state_dir: Path = STATE_DIR) -> PaperTradingState:
    path = _state_path(instrument, state_dir)
    if not path.exists():
        return PaperTradingState()
    raw = json.loads(path.read_text(encoding="utf-8"))
    return PaperTradingState(
        open_trade=_trade_from_dict(raw["open_trade"]) if raw.get("open_trade") else None,
        completed_trades=[_trade_from_dict(t) for t in raw.get("completed_trades", [])],
        last_processed_timestamp=raw.get("last_processed_timestamp"),
    )


def save_state(instrument: str, state: PaperTradingState, state_dir: Path = STATE_DIR) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    raw = {
        "open_trade": _trade_to_dict(state.open_trade) if state.open_trade else None,
        "completed_trades": [_trade_to_dict(t) for t in state.completed_trades],
        "last_processed_timestamp": state.last_processed_timestamp,
    }
    _state_path(instrument, state_dir).write_text(json.dumps(raw, indent=2), encoding="utf-8")
