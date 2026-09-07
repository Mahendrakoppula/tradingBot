import logging

from trading_bot.market_context import get_india_vix, get_oi_buildup, get_pcr
from trading_bot.rest_client import RestClient

log = logging.getLogger(__name__)


def _find_by_underlying(rows: list[dict] | None, underlying: str) -> dict | None:
    if not rows:
        return None
    underlying = underlying.upper()
    for row in rows:
        symbol = str(row.get("tradingSymbol", "")).upper()
        if symbol.startswith(underlying):
            return row
    return None


class TradeFilter:
    """VIX + PCR + OI-buildup go/no-go check for whether to enter a new
    condor today.

    This is a first-cut heuristic, NOT a validated edge - there's no
    backtesting behind these thresholds yet. Logic:

    - VIX must sit inside [vix_min, vix_max]: too low and there isn't enough
      premium to justify the trade; too high and it's flagging enough stress
      that a hard directional move (even through the hedge wings) is more
      likely than usual.
    - The underlying's PCR (from its futures contract) must sit inside
      [pcr_min, pcr_max]: near 1 is "balanced"; a PCR far from that suggests
      the market's already leaning hard one way, which cuts against a
      market-neutral structure like an iron condor.
    - The underlying must NOT be showing fresh "Long Built Up" or "Short
      Built Up" in the OI-buildup data: that means directional conviction is
      actively building right now, again arguing against a neutral trade.

    Watch what this actually decides (via the trade log / notifications)
    before trusting it - the thresholds are reasonable starting guesses, not
    tuned values.
    """

    def __init__(self, vix_min: float, vix_max: float, pcr_min: float, pcr_max: float):
        self.vix_min = vix_min
        self.vix_max = vix_max
        self.pcr_min = pcr_min
        self.pcr_max = pcr_max

    def should_trade(self, rest: RestClient, underlying: str) -> tuple[bool, str]:
        vix = get_india_vix(rest)
        if vix is None:
            return False, "could not fetch India VIX"
        if not (self.vix_min <= vix <= self.vix_max):
            return False, f"VIX {vix:.2f} outside [{self.vix_min},{self.vix_max}] band"

        pcr_rows = get_pcr(rest)
        pcr_row = _find_by_underlying(pcr_rows, underlying)
        if pcr_row is not None:
            pcr = float(pcr_row["pcr"])
            if not (self.pcr_min <= pcr <= self.pcr_max):
                return False, f"{underlying} PCR {pcr:.2f} outside [{self.pcr_min},{self.pcr_max}] band"
        else:
            log.warning("No PCR row found for %s - skipping PCR check", underlying)

        oi_buildup = get_oi_buildup(rest)
        for datatype in ("Long Built Up", "Short Built Up"):
            row = _find_by_underlying(oi_buildup.get(datatype), underlying)
            if row is not None:
                return False, f"{underlying} showing fresh {datatype} - directional momentum building"

        return True, f"VIX {vix:.2f}, PCR/OI checks passed"
