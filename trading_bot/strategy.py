import logging
from dataclasses import dataclass

from trading_bot.options import OptionChain, OptionContract
from trading_bot.rest_client import RestClient
from trading_bot.state import LegFill

log = logging.getLogger(__name__)


def place_split_order(
    rest: RestClient, tradingsymbol: str, token: str, exchange: str, transaction_type: str,
    total_qty: int, freeze_qty: int,
) -> None:
    """NSE rejects any single F&O order above the contract's freeze quantity -
    split into multiple orders when a trade's total size exceeds it. Shared
    by every strategy that places orders."""
    freeze_qty = freeze_qty or total_qty
    remaining = total_qty
    while remaining > 0:
        chunk = min(remaining, freeze_qty)
        order = {
            "variety": "NORMAL",
            "tradingsymbol": tradingsymbol,
            "symboltoken": token,
            "transactiontype": transaction_type,
            "exchange": exchange,
            "ordertype": "MARKET",
            "producttype": "INTRADAY",
            "duration": "DAY",
            "quantity": str(chunk),
        }
        log.info("Placing order: %s", order)
        rest.place_order(order)
        remaining -= chunk


def current_ltp(rest: RestClient, exchange: str, tradingsymbol: str, token: str) -> float:
    # Used as a proxy fill price for state/P&L tracking - for a MARKET order
    # this should be close to the actual fill, but isn't the real average
    # price. Fine for stop-loss/logging; don't rely on it for accounting.
    try:
        data = rest.get_ltp(exchange, tradingsymbol, token)
        return float(data["ltp"])
    except Exception:
        log.warning("Could not fetch LTP for %s after order - entry_price recorded as 0", tradingsymbol)
        return 0.0


@dataclass
class CondorLegs:
    short_call: OptionContract
    short_put: OptionContract
    hedge_call: OptionContract
    hedge_put: OptionContract


def build_iron_condor(
    chain: OptionChain,
    expiry,
    spot: float,
    otm_distance_pct: float,
    wing_distance_pct: float,
) -> CondorLegs:
    short_call_target = spot * (1 + otm_distance_pct)
    short_put_target = spot * (1 - otm_distance_pct)
    hedge_call_target = spot * (1 + otm_distance_pct + wing_distance_pct)
    hedge_put_target = spot * (1 - otm_distance_pct - wing_distance_pct)
    return CondorLegs(
        short_call=chain.nearest_strike(expiry, "CE", short_call_target),
        short_put=chain.nearest_strike(expiry, "PE", short_put_target),
        hedge_call=chain.nearest_strike(expiry, "CE", hedge_call_target),
        hedge_put=chain.nearest_strike(expiry, "PE", hedge_put_target),
    )


class IronCondorStrategy:
    """Enters/exits a same-day, defined-risk iron condor near expiry.

    NOTE (2026-09-07, verified against a live account): even hedged, this
    needs real margin - Rs.40k-95k+ per lot on every liquid NIFTY/BANKNIFTY/
    stock-option name checked, because Angel's margin calculator doesn't
    give full benefit to the hedge here. Not viable on small paper capital
    (e.g. Rs.50k) - see debit_strategy.LongOptionStrategy for the
    capital-efficient alternative (buying, not selling) that's actually
    wired into run_daily.py. This class is kept for if/when capital is
    raised enough to sell premium again.

    Entry buys the hedge (long) legs BEFORE selling the short legs, so the
    position is never naked even transiently if a later order fails partway
    through. Always uses INTRADAY product type - positions are squared off
    same day, never carried into expiry. This sidesteps stock-option
    physical-settlement risk and overnight gap risk, at the cost of only
    capturing one day's theta rather than riding the position across the
    whole DTE window.

    Orders are placed at MARKET. Near-expiry far-OTM contracts can be thin,
    so slippage on the hedge legs especially is a real risk this doesn't
    protect against yet - worth revisiting with limit orders priced off the
    quote API's depth once this has been dry-run tested.
    """

    def __init__(self, rest: RestClient):
        self.rest = rest

    def enter(self, legs: CondorLegs, qty_lots: int) -> dict[str, LegFill]:
        fills = {}
        fills["hedge_call"] = self._enter_leg(legs.hedge_call, "BUY", qty_lots)
        fills["hedge_put"] = self._enter_leg(legs.hedge_put, "BUY", qty_lots)
        fills["short_call"] = self._enter_leg(legs.short_call, "SELL", qty_lots)
        fills["short_put"] = self._enter_leg(legs.short_put, "SELL", qty_lots)
        return fills

    def exit(self, fills: dict[str, LegFill]) -> None:
        # Order doesn't matter for safety here (both sides are already on),
        # but close the shorts first so the position stops being short
        # anything as early as possible.
        self._close_leg(fills["short_call"])
        self._close_leg(fills["short_put"])
        self._close_leg(fills["hedge_call"])
        self._close_leg(fills["hedge_put"])

    def _enter_leg(self, contract: OptionContract, transaction_type: str, qty_lots: int) -> LegFill:
        total_qty = contract.lotsize * qty_lots
        place_split_order(self.rest, contract.tradingsymbol, contract.token, contract.exchange, transaction_type, total_qty, contract.freeze_qty)
        entry_price = current_ltp(self.rest, contract.exchange, contract.tradingsymbol, contract.token)
        return LegFill(
            tradingsymbol=contract.tradingsymbol,
            symboltoken=contract.token,
            exchange=contract.exchange,
            lotsize=contract.lotsize,
            freeze_qty=contract.freeze_qty,
            transaction_type=transaction_type,
            quantity=total_qty,
            entry_price=entry_price,
        )

    def _close_leg(self, leg: LegFill) -> None:
        closing_side = "BUY" if leg.transaction_type == "SELL" else "SELL"
        place_split_order(self.rest, leg.tradingsymbol, leg.symboltoken, leg.exchange, closing_side, leg.quantity, leg.freeze_qty)
