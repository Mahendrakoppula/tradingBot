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

# --- second bot: technical-indicator strategy (run_technical.py) ---------
# Fully isolated from every path above - a separate OS process, own capital
# ledger, own position files - see .claude/plans/goofy-plotting-sedgewick.md
# ("state files must be fully isolated from the live bot's to avoid
# file-write collisions between two independent OS processes").
TECHNICAL_SCALP_STATE_PATH = Path(__file__).resolve().parent.parent / ".state" / "technical_scalp_positions.json"
TECHNICAL_INTRADAY_STATE_PATH = Path(__file__).resolve().parent.parent / ".state" / "technical_intraday_positions.json"
TECHNICAL_SWING_EQUITY_STATE_PATH = Path(__file__).resolve().parent.parent / ".state" / "technical_swing_equity_positions.json"
TECHNICAL_SWING_OPTION_STATE_PATH = Path(__file__).resolve().parent.parent / ".state" / "technical_swing_option_positions.json"
TECHNICAL_CAPITAL_PATH = Path(__file__).resolve().parent.parent / ".state" / "technical_capital.json"


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


def count_trades_today(strategy_tag: str, today_iso: str) -> dict[str, int]:
    """Generic version of count_scalp_trades_today for any `strategy` tag -
    used by the technical bot's per-day trade caps (scalp/intraday) so they
    survive a mid-day restart instead of resetting to 0."""
    counts: dict[str, int] = {}
    if not TRADE_LOG_PATH.exists():
        return counts
    for line in TRADE_LOG_PATH.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("strategy") == strategy_tag and str(rec.get("closed_at", "")).startswith(today_iso):
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


@dataclass
class OpenTechnicalOption:
    """Scalp or intraday tier position - options only, same-day exit,
    mirrors OpenScalpOption's shape. `tier` distinguishes which one so a
    shared trade_log entry can be filtered later. `entry_spot` is the
    underlying's price at entry, kept for context/display only. `stop_price`/
    `target_price` are fixed rupee-per-lot levels converted to a PREMIUM
    price (see technical_strategy.premium_stop_target) - NOT underlying
    prices, unlike OpenTechnicalSwingOption's ATR-based fields, and not
    trailed (a hard stop/target, no trailing-stop mechanism for these two
    fast tiers - see run_technical.py's entry functions for why: a fixed
    rupee floor matters more here than letting a small win run further,
    given real transaction costs can erase a tiny gross gain, see costs.py).
    `stop_orderid`/`target_orderid` are the real exchange order ids for the
    broker-side protective orders once TECH_DRY_RUN=false - empty string in
    dry-run (place_order's stub response has no orderid) or before they've
    been placed."""
    underlying: str
    expiry: str  # "DDMMMYYYY", as in the scrip master
    entered_at: str  # ISO timestamp
    tier: str  # "scalp" | "intraday"
    signal_reason: str
    entry_spot: float
    stop_price: float
    target_price: float
    option: LegFill
    stop_orderid: str = ""
    target_orderid: str = ""


def load_technical_scalp() -> dict[str, OpenTechnicalOption]:
    return _load_technical_options(TECHNICAL_SCALP_STATE_PATH)


def save_technical_scalp(positions: dict[str, OpenTechnicalOption]) -> None:
    _save_technical_options(TECHNICAL_SCALP_STATE_PATH, positions)


def load_technical_intraday() -> dict[str, OpenTechnicalOption]:
    return _load_technical_options(TECHNICAL_INTRADAY_STATE_PATH)


def save_technical_intraday(positions: dict[str, OpenTechnicalOption]) -> None:
    _save_technical_options(TECHNICAL_INTRADAY_STATE_PATH, positions)


def _load_technical_options(path: Path) -> dict[str, OpenTechnicalOption]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    result = {}
    for underlying, o in raw.items():
        o = dict(o)
        o["option"] = LegFill(**o["option"])
        result[underlying] = OpenTechnicalOption(**o)
    if result:
        log.info("Loaded %d open technical option position(s) from %s", len(result), path)
    return result


def _save_technical_options(path: Path, positions: dict[str, OpenTechnicalOption]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = {u: asdict(o) for u, o in positions.items()}
    path.write_text(json.dumps(raw, indent=2), encoding="utf-8")


@dataclass
class OpenTechnicalSwingOption:
    """Swing tier position on an INDEX - options with a wide DTE window
    (multi-day hold, no physical-settlement risk since it's cash-settled),
    exited on trend-reversal rather than a same-day timer. `broken_level`,
    `entry_index_price`, `stop_price`/`target_price`/`favorable_extreme` are
    all index levels (not option premium) - the trend-reversal/stop/target/
    trailing checks compare the index's current level against them, same
    convention as backtest_technical.backtest_swing / technical_strategy.
    atr_stop_target. `stop_orderid`/`target_orderid` as in OpenTechnicalOption."""
    underlying: str
    expiry: str
    entered_at: str
    direction: str  # "long" | "short" - which side of the breakout this was
    broken_level: float  # the S/R level whose reclaim triggers the exit
    entry_index_price: float
    stop_price: float
    target_price: float
    atr_value: float
    favorable_extreme: float
    trailing_active: bool
    signal_reason: str
    option: LegFill
    stop_orderid: str = ""
    target_orderid: str = ""


def load_technical_swing_option() -> dict[str, OpenTechnicalSwingOption]:
    if not TECHNICAL_SWING_OPTION_STATE_PATH.exists():
        return {}
    raw = json.loads(TECHNICAL_SWING_OPTION_STATE_PATH.read_text(encoding="utf-8"))
    result = {}
    for underlying, o in raw.items():
        o = dict(o)
        o["option"] = LegFill(**o["option"])
        result[underlying] = OpenTechnicalSwingOption(**o)
    if result:
        log.info("Loaded %d open technical swing option position(s) from %s", len(result), TECHNICAL_SWING_OPTION_STATE_PATH)
    return result


def save_technical_swing_option(positions: dict[str, OpenTechnicalSwingOption]) -> None:
    TECHNICAL_SWING_OPTION_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    raw = {u: asdict(o) for u, o in positions.items()}
    TECHNICAL_SWING_OPTION_STATE_PATH.write_text(json.dumps(raw, indent=2), encoding="utf-8")


@dataclass
class OpenTechnicalEquity:
    """Swing tier position on a STOCK - real NSE CNC delivery shares (no
    theta/settlement risk since there's no option contract), exited on
    trend-reversal. Only ever "long" - no equity-shorting infra exists in
    this repo, so a "short" swing signal on a stock is skipped by the
    caller, never opened as a position. `stop_price`/`target_price`/
    `favorable_extreme` are the stock's own price (see
    technical_strategy.atr_stop_target) - sizing itself uses the ATR-derived
    stop distance too, see run_technical._maybe_swing_equity_enter."""
    underlying: str
    entered_at: str
    broken_level: float
    stop_price: float
    target_price: float
    atr_value: float
    favorable_extreme: float
    trailing_active: bool
    signal_reason: str
    equity: LegFill
    stop_orderid: str = ""
    target_orderid: str = ""


def load_technical_swing_equity() -> dict[str, OpenTechnicalEquity]:
    if not TECHNICAL_SWING_EQUITY_STATE_PATH.exists():
        return {}
    raw = json.loads(TECHNICAL_SWING_EQUITY_STATE_PATH.read_text(encoding="utf-8"))
    result = {}
    for underlying, o in raw.items():
        o = dict(o)
        o["equity"] = LegFill(**o["equity"])
        result[underlying] = OpenTechnicalEquity(**o)
    if result:
        log.info("Loaded %d open technical swing equity position(s) from %s", len(result), TECHNICAL_SWING_EQUITY_STATE_PATH)
    return result


def save_technical_swing_equity(positions: dict[str, OpenTechnicalEquity]) -> None:
    TECHNICAL_SWING_EQUITY_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    raw = {u: asdict(o) for u, o in positions.items()}
    TECHNICAL_SWING_EQUITY_STATE_PATH.write_text(json.dumps(raw, indent=2), encoding="utf-8")


def load_technical_capital(starting_capital: float) -> dict:
    """Separate paper-trading capital ledger for the technical bot - kept
    fully independent from the daily bot's CAPITAL_PATH (a genuinely
    separate 50k pool, not shared)."""
    if TECHNICAL_CAPITAL_PATH.exists():
        ledger = json.loads(TECHNICAL_CAPITAL_PATH.read_text(encoding="utf-8"))
        log.info("Loaded technical capital ledger: Rs.%.2f (started at Rs.%.2f)", ledger["current_capital"], ledger["starting_capital"])
        return ledger
    ledger = {"starting_capital": starting_capital, "current_capital": starting_capital, "updated_at": None}
    save_technical_capital(ledger)
    log.info("Initialized new technical paper-trading capital ledger at Rs.%.2f", starting_capital)
    return ledger


def save_technical_capital(ledger: dict) -> None:
    TECHNICAL_CAPITAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    TECHNICAL_CAPITAL_PATH.write_text(json.dumps(ledger, indent=2), encoding="utf-8")


def log_journal_day(record: dict) -> None:
    """Appends one day's structured market-conditions/decisions/outcomes
    summary - the raw material for periodic human review of whether the
    strategy needs adjusting. See README 'Daily journal' section."""
    JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with JOURNAL_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
