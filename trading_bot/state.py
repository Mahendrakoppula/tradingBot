import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)

STATE_PATH = Path(__file__).resolve().parent.parent / ".state" / "positions.json"
LONG_STATE_PATH = Path(__file__).resolve().parent.parent / ".state" / "long_positions.json"
SCALP_STATE_PATH = Path(__file__).resolve().parent.parent / ".state" / "scalp_positions.json"
CAPITAL_PATH = Path(__file__).resolve().parent.parent / ".state" / "capital.json"
TRADE_LOG_PATH = Path(__file__).resolve().parent.parent / ".state" / "trade_log.jsonl"
JOURNAL_PATH = Path(__file__).resolve().parent.parent / ".state" / "journal.jsonl"
NEWS_SENTIMENT_LOG_PATH = Path(__file__).resolve().parent.parent / ".state" / "news_sentiment_log.jsonl"


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
    entry_reason: str = ""  # why this trade was taken (OI-buildup/momentum signal) - default "" so a
                             # position saved by an older build (before this field existed) still loads


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


@dataclass
class OpenScalpOption:
    underlying: str
    expiry: str  # "DDMMMYYYY", as in the scrip master
    entered_at: str  # ISO timestamp
    signal_reason: str  # which scalp trigger fired (ORB breakout / momentum spike)
    option: LegFill


def load_scalp() -> dict[str, OpenScalpOption]:
    if not SCALP_STATE_PATH.exists():
        return {}
    raw = json.loads(SCALP_STATE_PATH.read_text(encoding="utf-8"))
    result = {}
    for underlying, o in raw.items():
        o = dict(o)
        o["option"] = LegFill(**o["option"])
        result[underlying] = OpenScalpOption(**o)
    if result:
        log.info("Loaded %d open scalp position(s) from %s", len(result), SCALP_STATE_PATH)
    return result


def save_scalp(positions: dict[str, OpenScalpOption]) -> None:
    SCALP_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    raw = {u: asdict(o) for u, o in positions.items()}
    SCALP_STATE_PATH.write_text(json.dumps(raw, indent=2), encoding="utf-8")


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


def count_scalp_trades_today(today_iso: str) -> dict[str, int]:
    """Per-underlying count of already-logged scalp trades for today, so the
    SCALP_MAX_TRADES_PER_DAY cap survives a mid-day restart (e.g. a CI
    redeploy) instead of resetting to 0 every time the process restarts.
    Only counts records with strategy == "scalp" - the existing daily
    strategy's trade_log entries have no "strategy" key and are never
    counted here."""
    counts: dict[str, int] = {}
    if not TRADE_LOG_PATH.exists():
        return counts
    for line in TRADE_LOG_PATH.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("strategy") == "scalp" and str(rec.get("closed_at", "")).startswith(today_iso):
            counts[rec["underlying"]] = counts.get(rec["underlying"], 0) + 1
    return counts


def log_trade(record: dict) -> None:
    """Appends one closed trade's outcome for later strategy review."""
    TRADE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with TRADE_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def log_news_sentiment(record: dict) -> None:
    """Appends one day's news-sentiment scan (news_sentiment.py) for future
    threshold-tuning once enough history exists - see premarket_bias.py's
    docstring: informational only right now, not wired into any gate."""
    NEWS_SENTIMENT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with NEWS_SENTIMENT_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def log_journal_day(record: dict) -> None:
    """Appends one day's structured market-conditions/decisions/outcomes
    summary - the raw material for periodic human review of whether the
    strategy needs adjusting. See README 'Daily journal' section."""
    JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with JOURNAL_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
