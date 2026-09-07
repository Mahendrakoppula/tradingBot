import json
import logging
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)

CACHE_PATH = Path(__file__).resolve().parent.parent / ".cache" / "scrip_master.json"
CACHE_TTL_SECONDS = 24 * 60 * 60  # scrip master is regenerated once a day


class InstrumentLookup:
    """Downloads and caches the daily scrip master, and resolves
    tradingsymbol -> symboltoken for placing orders / subscribing to feeds.
    """

    def __init__(self, scrip_master_url: str):
        self.scrip_master_url = scrip_master_url
        self.instruments: list[dict] = []
        self._by_symbol: dict[tuple[str, str], dict] = {}

    def load(self, force_refresh: bool = False) -> None:
        if not force_refresh and CACHE_PATH.exists():
            age = time.time() - CACHE_PATH.stat().st_mtime
            if age < CACHE_TTL_SECONDS:
                instruments = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
                self._index(instruments)
                log.info("Loaded %d instruments from cache", len(instruments))
                return

        resp = requests.get(self.scrip_master_url, timeout=30)
        resp.raise_for_status()
        instruments = resp.json()

        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(instruments), encoding="utf-8")

        self._index(instruments)
        log.info("Downloaded and cached %d instruments", len(instruments))

    def _index(self, instruments: list[dict]) -> None:
        self.instruments = instruments
        self._by_symbol = {(i["exch_seg"], i["symbol"]): i for i in instruments}

    def find(self, exch_seg: str, tradingsymbol: str) -> dict:
        """exch_seg examples: nse_cm, nse_fo, bse_cm, bse_fo, mcx_fo."""
        try:
            return self._by_symbol[(exch_seg, tradingsymbol)]
        except KeyError:
            raise KeyError(f"No instrument found for {exch_seg}/{tradingsymbol}") from None
