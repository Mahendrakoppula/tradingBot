import datetime as dt
import json
import logging
import time
from pathlib import Path

from trading_bot.instruments import InstrumentLookup
from trading_bot.options import OptionChain, OptionContract, find_spot_instrument
from trading_bot.rest_client import RestClient

log = logging.getLogger(__name__)

LOG_DIR = Path(__file__).resolve().parent.parent / ".state" / "candle_log"

# Historical getCandleData is rate-limited to 3 req/sec, 150/min, 5000/day
# (docs/smartapi-reference.md "Rate Limits" table) - getOIData isn't listed
# separately there, so treated as sharing that same budget to be safe
# (this project has repeatedly found real limits stricter/different than
# documented - see research/README.md and option_chain_logger.py). Each
# (contract, interval) pull is 2 calls (candle + OI); pacing at 0.5s/call
# stays comfortably under 3/sec even with jitter.
CANDLE_CALL_SLEEP_SECONDS = 0.5

# Each interval pulls its OWN cadence, not every-N-minutes-for-everything -
# getCandleData is a batch/range endpoint (one call returns the whole day's
# series so far), so there's no need to poll it every 5 minutes the way the
# point-sample option_chain_logger does. Cadences are staggered so only ONE
# interval is ever due per main-loop tick (see run_daily.py) - bounds how
# long any single 30s tick can be extended by this logging.
INTERVAL_CONFIG = {
    "ONE_MINUTE": {"cadence_minutes": 30, "lookback_days": None},   # None = "today so far"
    "FIVE_MINUTE": {"cadence_minutes": 60, "lookback_days": None},
    "TEN_MINUTE": {"cadence_minutes": 60, "lookback_days": None},
    "THIRTY_MINUTE": {"cadence_minutes": 120, "lookback_days": None},
    "ONE_DAY": {"cadence_minutes": 240, "lookback_days": 60},
}


def select_tracked_contracts(chain: OptionChain, expiry: dt.date, spot: float, strikes_each_side: int) -> list[OptionContract]:
    """Picks a FIXED band of strikes around ATM (both CE and PE) - fixed for
    the whole day once resolved (see run_daily.py's daily-reset of the
    cache this feeds), so the day's candle series stay clean/continuous for
    a stable set of contracts rather than fragmenting across many strikes
    as spot drifts through the day.
    """
    contracts = chain.for_expiry(expiry)
    strikes = sorted({c.strike for c in contracts})
    if not strikes:
        return []
    atm_idx = min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot))
    lo = max(0, atm_idx - strikes_each_side)
    hi = min(len(strikes), atm_idx + strikes_each_side + 1)
    tracked_strikes = set(strikes[lo:hi])
    return [c for c in contracts if c.strike in tracked_strikes]


def fetch_candles_with_oi(rest: RestClient, contract: OptionContract, interval: str, fromdate: str, todate: str) -> list[dict]:
    """OHLCV (get_candle_data) merged with OI (get_oi_data, a separate
    endpoint - candles have no OI field) by matching ISO timestamps. Never
    raises for the OI half - a contract not yet old enough for OI data (or
    any other OI-specific hiccup) still logs candles with oi=None rather
    than losing the whole pull.
    """
    candles = rest.get_candle_data(contract.exchange, contract.token, interval, fromdate, todate) or []
    rows = [
        {"time": row[0], "open": row[1], "high": row[2], "low": row[3], "close": row[4], "volume": row[5], "oi": None}
        for row in candles
    ]
    try:
        oi_rows = rest.get_oi_data(contract.exchange, contract.token, interval, fromdate, todate) or []
        oi_by_time = {r["time"]: r["oi"] for r in oi_rows}
        for row in rows:
            if row["time"] in oi_by_time:
                row["oi"] = oi_by_time[row["time"]]
    except Exception:
        log.exception("OI fetch failed for %s %s - candles logged without OI", contract.tradingsymbol, interval)
    return rows


def _log_path(underlying: str, contract: OptionContract, interval: str, today: dt.date) -> Path:
    return LOG_DIR / f"{underlying}_{contract.tradingsymbol}_{interval}_{today.isoformat()}.json"


def log_candles(underlying: str, contract: OptionContract, interval: str, today: dt.date, rows: list[dict]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "underlying": underlying,
        "tradingsymbol": contract.tradingsymbol,
        "token": contract.token,
        "strike": contract.strike,
        "option_type": contract.option_type,
        "expiry": contract.expiry.isoformat(),
        "interval": interval,
        "updated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "candles": rows,
    }
    _log_path(underlying, contract, interval, today).write_text(json.dumps(record), encoding="utf-8")


def maybe_log_candles(rest: RestClient, instruments: InstrumentLookup, watchlist: tuple[str, ...],
                       dte_min: int, dte_max: int, today: dt.date, strikes_each_side: int,
                       tracked_contracts_cache: dict, last_pull_at: dict) -> None:
    """Call every main-loop tick - internally a no-op unless some interval's
    cadence is due. Processes AT MOST ONE due interval per call (bounds
    worst-case added latency on any single tick to one interval's contract
    list, not all five stacked together). Never raises.

    An interval with no entry yet in last_pull_at is ALWAYS due immediately
    - found via a real CI failure on a fresh runner (2026-09-08): comparing
    against a 0.0 default assumes time.monotonic() is already past every
    cadence threshold at process start, which isn't guaranteed (it's time
    since an arbitrary reference point, often low system uptime on a fresh
    container OR a freshly-booted EC2 instance - this bot's own instance
    reboots fresh every morning via EventBridge). Without this fix, the
    first candle pull of the day could silently be delayed by up to
    240 minutes instead of firing right away.
    """
    due_interval = next(
        (name for name, cfg in INTERVAL_CONFIG.items()
         if name not in last_pull_at or time.monotonic() - last_pull_at[name] >= cfg["cadence_minutes"] * 60),
        None,
    )
    if due_interval is None:
        return
    last_pull_at[due_interval] = time.monotonic()
    cfg = INTERVAL_CONFIG[due_interval]

    now = dt.datetime.now()
    if cfg["lookback_days"] is None:
        fromdate = dt.datetime.combine(today, dt.time(9, 15))
    else:
        fromdate = dt.datetime.combine(today, dt.time(9, 15)) - dt.timedelta(days=cfg["lookback_days"])
    fromdate_str, todate_str = fromdate.strftime("%Y-%m-%d %H:%M"), now.strftime("%Y-%m-%d %H:%M")

    for underlying in watchlist:
        try:
            if underlying not in tracked_contracts_cache:
                chain = OptionChain(instruments.instruments, underlying, exchange="NFO")
                expiry = chain.nearest_expiry_within(today, dte_min, dte_max)
                if expiry is None:
                    continue
                spot_row = find_spot_instrument(instruments.instruments, underlying)
                spot = float(rest.get_ltp(spot_row["exch_seg"], spot_row["symbol"], spot_row["token"])["ltp"])
                tracked_contracts_cache[underlying] = select_tracked_contracts(chain, expiry, spot, strikes_each_side)

            contracts = tracked_contracts_cache[underlying]
            for contract in contracts:
                try:
                    rows = fetch_candles_with_oi(rest, contract, due_interval, fromdate_str, todate_str)
                    log_candles(underlying, contract, due_interval, today, rows)
                except Exception:
                    log.exception("Candle history fetch failed for %s %s %s", underlying, contract.tradingsymbol, due_interval)
                time.sleep(CANDLE_CALL_SLEEP_SECONDS)
            log.info("Candle history logged: %s %s, %d contract(s)", underlying, due_interval, len(contracts))
        except Exception:
            log.exception("Candle history logging failed for %s (%s)", underlying, due_interval)
