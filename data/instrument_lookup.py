"""Scrip master download/cache and spot-instrument resolution for the
three in-scope indices (NIFTY, BANKNIFTY, SENSEX). Adapted from
trading_bot/instruments.py + trading_bot/options.py's find_spot_instrument
on `main` (verified live against a real scrip master dump - see that
module's docstring) rather than re-deriving the instrumenttype/exch_seg
rules from scratch.
"""
import json
import logging
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)

CACHE_PATH = Path(__file__).resolve().parent.parent / ".cache" / "codex_scrip_master.json"
CACHE_TTL_SECONDS = 24 * 60 * 60  # scrip master is regenerated once a day

# Verified live (see trading_bot/options.py): most indices are NSE, SENSEX
# is BSE - both are checked, in this order.
INDEX_EXCHANGES = ("NSE", "BSE")


class InstrumentLookup:
    def __init__(self, scrip_master_url: str, cache_path: Path = CACHE_PATH):
        self.scrip_master_url = scrip_master_url
        self.cache_path = cache_path
        self.instruments: list[dict] = []

    def load(self, force_refresh: bool = False) -> None:
        if not force_refresh and self.cache_path.exists():
            age = time.time() - self.cache_path.stat().st_mtime
            if age < CACHE_TTL_SECONDS:
                self.instruments = json.loads(self.cache_path.read_text(encoding="utf-8"))
                log.info("Loaded %d instruments from cache", len(self.instruments))
                return

        resp = requests.get(self.scrip_master_url, timeout=30)
        resp.raise_for_status()
        self.instruments = resp.json()

        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self.instruments), encoding="utf-8")
        log.info("Downloaded and cached %d instruments", len(self.instruments))


def find_spot_instrument(instruments: list[dict], underlying: str) -> dict:
    """Index-only (Phase 2 scope: NIFTY/BANKNIFTY/SENSEX) - instrumenttype
    "AMXIDX", exch_seg NSE or BSE. Raises LookupError if not found rather
    than guessing - a wrong token silently fetches the wrong instrument's
    history, which is worse than a loud failure."""
    underlying = underlying.upper()
    for exch_seg in INDEX_EXCHANGES:
        for row in instruments:
            if (
                str(row.get("name", "")).upper() == underlying
                and row.get("instrumenttype") == "AMXIDX"
                and row.get("exch_seg") == exch_seg
            ):
                return row
    raise LookupError(f"No spot instrument found for index {underlying}")
