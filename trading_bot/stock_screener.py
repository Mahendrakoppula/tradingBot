"""F&O-eligible stock universe for the swing tier's equity leg. PE screening
is explicitly NOT required for v1 - the real requirement is just "eligible
for F&O" (see .claude/plans/goofy-plotting-sedgewick.md decision #2) - a
stock needs listed options to belong to this bot's universe at all (mirrors
the scalp/intraday tiers' options requirement), and a volume filter on top
trims the ~210-name F&O universe down to names actually worth scanning
daily.

Deliberately NOT importing research/fetch_historical.py's own
resolve_fo_stock_tokens (near-identical logic) - that script's docstring
says it's research-only and not part of the live trading_bot package.
"""
import logging
import time

from trading_bot.options import find_spot_instrument
from trading_bot.rest_client import RestClient

log = logging.getLogger(__name__)

QUOTE_BATCH_SIZE = 50  # API limit per exchange per request


def fo_eligible_stock_names(instruments: list[dict]) -> list[str]:
    """Every F&O-eligible stock underlying (instrumenttype OPTSTK), sorted."""
    return sorted({
        r["name"] for r in instruments
        if r.get("instrumenttype") == "OPTSTK" and r.get("exch_seg") == "NFO"
    })


def resolve_spot_rows(instruments: list[dict], names: list[str]) -> list[dict]:
    rows = []
    for name in names:
        try:
            rows.append(find_spot_instrument(instruments, name))
        except LookupError:
            log.warning("No equity spot instrument found for F&O stock %s - skipping from universe", name)
    return rows


def filter_by_volume(rest: RestClient, rows: list[dict], min_volume: int) -> list[dict]:
    """Keeps only rows whose today's tradeVolume >= min_volume - the F&O
    universe includes plenty of thin names not worth a daily swing scan."""
    if not rows:
        return []
    tokens = [row["token"] for row in rows]
    kept_tokens: set[str] = set()
    for i in range(0, len(tokens), QUOTE_BATCH_SIZE):
        batch = tokens[i : i + QUOTE_BATCH_SIZE]
        if i > 0:
            time.sleep(0.3)
        data = rest.get_quote("FULL", {"NSE": batch})
        for q in data.get("fetched", []):
            if int(float(q.get("tradeVolume", 0) or 0)) >= min_volume:
                kept_tokens.add(q["symbolToken"])
    return [row for row in rows if row["token"] in kept_tokens]


def build_universe(rest: RestClient, instruments: list[dict], min_volume: int) -> list[dict]:
    """One-call convenience: F&O-eligible names -> resolve spot rows -> volume
    filter. Returns spot instrument rows (name/token/exch_seg/symbol)."""
    names = fo_eligible_stock_names(instruments)
    rows = resolve_spot_rows(instruments, names)
    return filter_by_volume(rest, rows, min_volume)
