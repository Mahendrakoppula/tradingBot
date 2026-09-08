import datetime as dt
import json
import logging
import time
from pathlib import Path

from trading_bot.instruments import InstrumentLookup
from trading_bot.options import OptionChain, find_spot_instrument
from trading_bot.rest_client import RestClient

log = logging.getLogger(__name__)

LOG_DIR = Path(__file__).resolve().parent.parent / ".state" / "option_chain_log"

# Market Data API: max 50 symbols/request, rate limit 1 req/sec (see
# docs/smartapi-reference.md "Market Data API"). Sleeping between batches
# here is deliberate, not just polite - this call already runs inside
# run_daily's loop, which also calls get_ltp/get_oi_buildup elsewhere.
QUOTE_BATCH_SIZE = 50
QUOTE_BATCH_SLEEP_SECONDS = 1.1

# Option Greeks and PCR are both documented/observed around 1 req/sec (see
# docs/smartapi-reference.md "Option Greeks" and the OIBuildup/gainersLosers
# note in the same file) - same conservative pacing as the quote batches.
GREEKS_SLEEP_SECONDS = 1.1


def _log_path(underlying: str, today: dt.date) -> Path:
    return LOG_DIR / f"{underlying}_{today.isoformat()}.jsonl"


def _contracts_near_spot(chain: OptionChain, expiry: dt.date, spot: float, band_pct: float) -> list:
    lo, hi = spot * (1 - band_pct), spot * (1 + band_pct)
    return [c for c in chain.for_expiry(expiry) if lo <= c.strike <= hi]


def _fetch_quotes(rest: RestClient, tokens: list[str]) -> dict[str, dict]:
    """Batched FULL-mode quotes, keyed by symbolToken. Never raises - a
    logging failure must never affect the live strategy loop that calls
    this alongside it."""
    result: dict[str, dict] = {}
    for i in range(0, len(tokens), QUOTE_BATCH_SIZE):
        batch = tokens[i : i + QUOTE_BATCH_SIZE]
        try:
            resp = rest.get_quote("FULL", {"NFO": batch})
            for row in resp.get("data", {}).get("fetched", []):
                result[row["symbolToken"]] = row
        except Exception:
            log.exception("Option chain snapshot: quote batch failed (tokens %s..)", batch[0] if batch else "?")
        if i + QUOTE_BATCH_SIZE < len(tokens):
            time.sleep(QUOTE_BATCH_SLEEP_SECONDS)
    return result


def _fetch_iv_lookup(rest: RestClient, underlying: str, expiry: dt.date) -> dict[tuple[float, str], float]:
    """(strike, option_type) -> impliedVolatility for one underlying+expiry,
    via the Option Greeks endpoint (one call covers the WHOLE chain - verified
    live 2026-09-08: strikePrice is already plain-scaled, e.g. "23700.000000",
    matches OptionContract.strike directly, no /100 conversion needed unlike
    the scrip master's raw strike field). Never raises - returns {} on any
    failure, same "logging must never break the caller" contract as the rest
    of this module. Feeds a future IV-rank signal once enough history exists
    - not used for any trading decision yet (see config.py's candle_log/
    option_chain_log comments: pure data collection at this stage)."""
    try:
        rows = rest.get_option_greeks(underlying, expiry.strftime("%d%b%Y").upper())
        return {(float(r["strikePrice"]), r["optionType"]): float(r["impliedVolatility"]) for r in rows}
    except Exception:
        log.exception("Option chain snapshot: IV fetch failed for %s %s", underlying, expiry)
        return {}


def _fetch_pcr_by_underlying(rest: RestClient) -> dict[str, float]:
    """underlying -> PCR, from the single market-wide putCallRatio call
    (covers every underlying at once, matched by tradingSymbol prefix - same
    pattern as debit_strategy._find_by_underlying). Never raises."""
    try:
        rows = rest.get_pcr()
    except Exception:
        log.exception("Option chain snapshot: PCR fetch failed")
        return {}
    result: dict[str, float] = {}
    for row in rows:
        symbol = str(row.get("tradingSymbol", "")).upper()
        for underlying in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"):
            if symbol.startswith(underlying) and underlying not in result:
                result[underlying] = row.get("pcr")
    return result


def log_snapshot(rest: RestClient, instruments: InstrumentLookup, watchlist: tuple[str, ...],
                  dte_min: int, dte_max: int, today: dt.date, strike_band_pct: float) -> None:
    """Appends one option-chain snapshot per underlying to
    .state/option_chain_log/<UNDERLYING>_<date>.jsonl - the raw material for
    a future premium-based backtest (see research/README.md: SmartAPI has no
    historical data for expired option contracts, so this is the only way to
    accumulate real premium history going forward). Also attaches per-strike
    implied volatility (Option Greeks endpoint) and the underlying's PCR -
    feeds a future IV-rank/PCR-trend signal once enough history exists
    (explicit user request); pure data collection at this stage, does not
    affect any trading decision. Never raises - called from the main loop
    alongside real trading logic, and a logging hiccup must never interrupt
    that.
    """
    pcr_by_underlying = _fetch_pcr_by_underlying(rest)  # one call covers every underlying
    time.sleep(GREEKS_SLEEP_SECONDS)

    for underlying in watchlist:
        try:
            chain = OptionChain(instruments.instruments, underlying, exchange="NFO")
            expiry = chain.nearest_expiry_within(today, dte_min, dte_max)
            if expiry is None:
                continue

            spot_row = find_spot_instrument(instruments.instruments, underlying)
            spot = float(rest.get_ltp(spot_row["exch_seg"], spot_row["symbol"], spot_row["token"])["ltp"])

            contracts = _contracts_near_spot(chain, expiry, spot, strike_band_pct)
            if not contracts:
                continue
            quotes = _fetch_quotes(rest, [c.token for c in contracts])
            iv_lookup = _fetch_iv_lookup(rest, underlying, expiry)
            time.sleep(GREEKS_SLEEP_SECONDS)

            rows = []
            for c in contracts:
                q = quotes.get(c.token)
                if q is None:
                    continue
                depth = q.get("depth", {})
                best_bid = depth.get("buy", [{}])[0].get("price") if depth.get("buy") else None
                best_ask = depth.get("sell", [{}])[0].get("price") if depth.get("sell") else None
                rows.append({
                    "strike": c.strike,
                    "option_type": c.option_type,
                    "tradingsymbol": c.tradingsymbol,
                    "token": c.token,
                    "ltp": q.get("ltp"),
                    "open": q.get("open"),
                    "high": q.get("high"),
                    "low": q.get("low"),
                    "close": q.get("close"),
                    "oi": q.get("opnInterest"),
                    "volume": q.get("tradeVolume"),
                    "best_bid": best_bid,
                    "best_ask": best_ask,
                    "iv": iv_lookup.get((c.strike, c.option_type)),
                })

            record = {
                "time": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                "underlying": underlying,
                "expiry": expiry.isoformat(),
                "spot": spot,
                "pcr": pcr_by_underlying.get(underlying),
                "contracts": rows,
            }
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            with _log_path(underlying, today).open("a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
            log.info("Option chain snapshot logged: %s %s, %d contracts near spot=%.2f", underlying, expiry, len(rows), spot)
        except Exception:
            log.exception("Option chain snapshot failed for %s - continuing", underlying)
