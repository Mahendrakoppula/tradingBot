"""India VIX + PCR + OI buildup - SmartAPI-native, no new credentials
beyond the broker session codex already has (data/broker_client.py).
Adapted from `main`'s trading_bot/market_context.py, whose token/endpoint
choices were verified against a live scrip master dump - reused rather
than re-derived.

LIVE-ONLY, informational only: no historical VIX/PCR/OI series has been
pulled (data/pull_history.py only covers spot OHLCV), so none of this
can be backtested the way market_state/ was. NOT wired into any trading
decision - the same "collect and log first, gate later once proven"
discipline `main`'s own premarket_bias.py explicitly established for
this exact data. A future phase would need real historical VIX/PCR data
before any of this could honestly feed a validated signal.
"""
import logging
import time

from data.broker_client import LiveMarketDataClient

log = logging.getLogger(__name__)

INDIA_VIX_TOKEN = "99926017"  # verified against a live scrip master dump, exch NSE (see main's market_context.py)

# PCR/OIBuildup aren't covered by SmartAPI's documented rate-limit table -
# verified live on `main` that back-to-back calls get HTTP 403 "exceeding
# access rate" without this pacing.
MARKET_DATA_CALL_INTERVAL_SECONDS = 1.2

OI_BUILDUP_DATATYPES = ("Long Built Up", "Short Built Up", "Short Covering", "Long Unwinding")


def get_india_vix(client: LiveMarketDataClient) -> float | None:
    try:
        data = client.get_ltp("NSE", "India VIX", INDIA_VIX_TOKEN)
        return float(data["ltp"])
    except Exception:
        log.exception("Could not fetch India VIX")
        return None


def get_pcr(client: LiveMarketDataClient) -> list | None:
    try:
        return client.get_pcr()
    except Exception:
        log.exception("Could not fetch PCR")
        return None


def get_oi_buildup(client: LiveMarketDataClient) -> dict[str, list]:
    """Covers ALL underlyings in each of the 4 calls - fetch once per
    cycle and reuse the result for every underlying needed, rather than
    calling this per-underlying."""
    result = {}
    for datatype in OI_BUILDUP_DATATYPES:
        time.sleep(MARKET_DATA_CALL_INTERVAL_SECONDS)
        try:
            result[datatype] = client.get_oi_buildup(expirytype="NEAR", datatype=datatype)
        except Exception:
            log.exception("Could not fetch OI buildup for %s", datatype)
    return result
