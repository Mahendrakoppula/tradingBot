import logging

from trading_bot.options import OptionChain, OptionContract
from trading_bot.rest_client import RestClient
from trading_bot.state import LegFill
from trading_bot.strategy import current_ltp, place_split_order

log = logging.getLogger(__name__)

# Reuses the exchange's OI-buildup categories as a directional proxy: fresh
# long positioning (or shorts covering) -> lean bullish (buy calls); fresh
# short positioning (or longs unwinding) -> lean bearish (buy puts). This is
# a first-cut heuristic, NOT backtested - it's what determines both whether
# there's "a possibility of a trade" today and which direction to take it.
DIRECTION_FROM_BUILDUP = {
    "Long Built Up": "CE",
    "Short Covering": "CE",
    "Short Built Up": "PE",
    "Long Unwinding": "PE",
}


def _find_by_underlying(rows: list[dict] | None, underlying: str) -> dict | None:
    if not rows:
        return None
    underlying = underlying.upper()
    for row in rows:
        symbol = str(row.get("tradingSymbol", "")).upper()
        if symbol.startswith(underlying):
            return row
    return None


def pick_direction(oi_buildup: dict[str, list], underlying: str) -> tuple[str | None, str]:
    """Decides CE or PE from today's OI buildup for this underlying, given
    an already-fetched oi_buildup snapshot (see market_context.get_oi_buildup -
    it covers every underlying in 4 calls total, so fetch it ONCE per cycle
    and reuse it here for each underlying, rather than re-fetching per
    underlying - this endpoint is rate-limited to ~1 req/sec, confirmed live).
    Returns (None, reason) if there's no signal - that's "no possibility of
    a trade today" for this underlying.
    """
    for datatype, option_type in DIRECTION_FROM_BUILDUP.items():
        if _find_by_underlying(oi_buildup.get(datatype), underlying) is not None:
            return option_type, f"{underlying} showing {datatype} -> buy {option_type}"
    return None, f"no OI-buildup signal for {underlying} today"


def pick_momentum_direction(spot_data: dict, min_move_pct: float) -> tuple[str | None, str]:
    """Fallback direction signal, used when OI buildup has nothing for this
    underlying - which is ALWAYS true for indices: verified live that
    OIBuildup only ever returns stock futures, never NIFTY/BANKNIFTY, since
    it's a top-10-per-category "movers" list, not a per-symbol lookup.

    Uses the same get_ltp response already fetched for spot pricing (it
    includes today's `open` for free) - no extra API call. Simple momentum:
    up min_move_pct% from today's open -> buy calls, down -> buy puts, else
    no signal (too flat to call a direction). First-cut heuristic, not
    backtested.
    """
    open_price = float(spot_data.get("open") or 0)
    ltp = float(spot_data["ltp"])
    if open_price <= 0:
        return None, "no valid open price for momentum check"

    pct_change = (ltp - open_price) / open_price * 100
    if pct_change >= min_move_pct:
        return "CE", f"up {pct_change:.2f}% from today's open -> buy CE"
    if pct_change <= -min_move_pct:
        return "PE", f"down {pct_change:.2f}% from today's open -> buy PE"
    return None, f"only {pct_change:.2f}% from open, inside +/-{min_move_pct}% band - no clear direction"


def build_long_leg(chain: OptionChain, expiry, spot: float, option_type: str, otm_distance_pct: float) -> OptionContract:
    target = spot * (1 + otm_distance_pct) if option_type == "CE" else spot * (1 - otm_distance_pct)
    return chain.nearest_strike(expiry, option_type, target)


class LongOptionStrategy:
    """Buys a single OTM option (CE or PE) based on today's OI-buildup
    direction signal - no spread, no hedge leg.

    Max loss is naturally capped at the premium paid: no margin is needed
    (verified against a live account - a naked long option's margin
    requirement is essentially just its premium), which is what makes this
    viable on small capital, unlike IronCondorStrategy (needs real margin,
    Rs.40k-95k+ per lot even hedged). A debit SPREAD was also considered and
    rejected: Angel's margin calculator doesn't net the short leg's risk
    against the long leg for a vertical spread, so it costs MORE margin
    (~Rs.32k, verified live) than just buying the single leg outright.

    Same-day only, same as the condor strategy: closed at EXIT_TIME or on
    hitting the stop-loss, never carried overnight.
    """

    def __init__(self, rest: RestClient):
        self.rest = rest

    def enter(self, contract: OptionContract, qty_lots: int) -> LegFill:
        total_qty = contract.lotsize * qty_lots
        place_split_order(self.rest, contract.tradingsymbol, contract.token, contract.exchange, "BUY", total_qty, contract.freeze_qty)
        entry_price = current_ltp(self.rest, contract.exchange, contract.tradingsymbol, contract.token)
        return LegFill(
            tradingsymbol=contract.tradingsymbol,
            symboltoken=contract.token,
            exchange=contract.exchange,
            lotsize=contract.lotsize,
            freeze_qty=contract.freeze_qty,
            transaction_type="BUY",
            quantity=total_qty,
            entry_price=entry_price,
        )

    def exit(self, leg: LegFill) -> None:
        place_split_order(self.rest, leg.tradingsymbol, leg.symboltoken, leg.exchange, "SELL", leg.quantity, leg.freeze_qty)
