import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)

STATE_PATH = Path(__file__).resolve().parent.parent / ".state" / "positions.json"
LONG_STATE_PATH = Path(__file__).resolve().parent.parent / ".state" / "long_positions.json"
CAPITAL_PATH = Path(__file__).resolve().parent.parent / ".state" / "capital.json"
TRADE_LOG_PATH = Path(__file__).resolve().parent.parent / ".state" / "trade_log.jsonl"
JOURNAL_PATH = Path(__file__).resolve().parent.parent / ".state" / "journal.jsonl"


@dataclass
class LegFill:
    tradingsymbol: str
    symboltoken: str
    exchange: str
    lotsize: int
    freeze_qty: int
    transaction_type: str  # BUY or SELL, as originally entered
    quantity: int
    entry_price: float = 0.0


@dataclass
class OpenCondor:
    underlying: str
    expiry: str  # "DDMMMYYYY", as in the scrip master
    entered_at: str  # ISO timestamp
    short_call: LegFill
    short_put: LegFill
    hedge_call: LegFill
    hedge_put: LegFill


def load() -> dict[str, OpenCondor]:
    if not STATE_PATH.exists():
        return {}
    raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    result = {}
    for underlying, c in raw.items():
        c = dict(c)
        for leg_name in ("short_call", "short_put", "hedge_call", "hedge_put"):
            c[leg_name] = LegFill(**c[leg_name])
        result[underlying] = OpenCondor(**c)
    if result:
        log.info("Loaded %d open position(s) from %s", len(result), STATE_PATH)
    return result


def save(positions: dict[str, OpenCondor]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    raw = {u: asdict(c) for u, c in positions.items()}
    STATE_PATH.write_text(json.dumps(raw, indent=2), encoding="utf-8")


@dataclass
class OpenLongOption:
    underlying: str
    expiry: str  # "DDMMMYYYY", as in the scrip master
    entered_at: str  # ISO timestamp
    option: LegFill


def load_long() -> dict[str, OpenLongOption]:
    if not LONG_STATE_PATH.exists():
        return {}
    raw = json.loads(LONG_STATE_PATH.read_text(encoding="utf-8"))
    result = {}
    for underlying, o in raw.items():
        o = dict(o)
        o["option"] = LegFill(**o["option"])
        result[underlying] = OpenLongOption(**o)
    if result:
        log.info("Loaded %d open long option(s) from %s", len(result), LONG_STATE_PATH)
    return result


def save_long(positions: dict[str, OpenLongOption]) -> None:
    LONG_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    raw = {u: asdict(o) for u, o in positions.items()}
    LONG_STATE_PATH.write_text(json.dumps(raw, indent=2), encoding="utf-8")


def load_capital(starting_capital: float) -> dict:
    """Paper-trading capital ledger. Created once at `starting_capital` on
    first run; every run after that (including a restart mid-day) picks up
    wherever the running balance left off, ignoring `starting_capital`.
    """
    if CAPITAL_PATH.exists():
        ledger = json.loads(CAPITAL_PATH.read_text(encoding="utf-8"))
        log.info("Loaded capital ledger: Rs.%.2f (started at Rs.%.2f)", ledger["current_capital"], ledger["starting_capital"])
        return ledger
    ledger = {"starting_capital": starting_capital, "current_capital": starting_capital, "updated_at": None}
    save_capital(ledger)
    log.info("Initialized new paper-trading capital ledger at Rs.%.2f", starting_capital)
    return ledger


def save_capital(ledger: dict) -> None:
    CAPITAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    CAPITAL_PATH.write_text(json.dumps(ledger, indent=2), encoding="utf-8")


def log_trade(record: dict) -> None:
    """Appends one closed trade's outcome for later strategy review."""
    TRADE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with TRADE_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def log_journal_day(record: dict) -> None:
    """Appends one day's structured market-conditions/decisions/outcomes
    summary - the raw material for periodic human review of whether the
    strategy needs adjusting. See README 'Daily journal' section."""
    JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with JOURNAL_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
