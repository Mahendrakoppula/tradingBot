import logging

from trading_bot.liquidity import entry_limit_price, exit_limit_price
from trading_bot.rest_client import RestClient
from trading_bot.state import LegFill
from trading_bot.strategy import current_ltp

log = logging.getLogger(__name__)


class EquityDeliveryStrategy:
    """Buys/sells whole shares via NSE CNC delivery (producttype DELIVERY) -
    the swing tier's equity leg for stocks (see
    .claude/plans/goofy-plotting-sedgewick.md decision #1: a multi-day
    equity hold avoids the theta decay/settlement risk a stock OPTION would
    carry over the same holding period; index swing uses options instead,
    reusing LongOptionStrategy with a wide DTE window).

    No freeze-qty splitting - that's an F&O-specific exchange rule, not a
    cash-equity concept.
    """

    def __init__(self, rest: RestClient, limit_buffer_pct: float = 0.5):
        self.rest = rest
        self.limit_buffer_pct = limit_buffer_pct

    def _place(self, tradingsymbol: str, token: str, exchange: str, transaction_type: str,
               qty: int, quote: dict | None) -> float | None:
        order = {
            "variety": "NORMAL",
            "tradingsymbol": tradingsymbol,
            "symboltoken": token,
            "transactiontype": transaction_type,
            "exchange": exchange,
            "producttype": "DELIVERY",
            "duration": "DAY",
            "quantity": str(qty),
        }
        price = None
        if quote is not None:
            price = entry_limit_price(quote, self.limit_buffer_pct) if transaction_type == "BUY" else exit_limit_price(quote, self.limit_buffer_pct)
            order["ordertype"] = "LIMIT"
            order["price"] = str(price)
        else:
            order["ordertype"] = "MARKET"
        log.info("Placing equity order: %s", order)
        self.rest.place_order(order)
        return price

    def enter(self, tradingsymbol: str, token: str, exchange: str, qty: int, quote: dict | None = None) -> LegFill:
        price = self._place(tradingsymbol, token, exchange, "BUY", qty, quote)
        entry_price = price if price is not None else current_ltp(self.rest, exchange, tradingsymbol, token)
        return LegFill(
            tradingsymbol=tradingsymbol, symboltoken=token, exchange=exchange,
            lotsize=1, freeze_qty=0, transaction_type="BUY", quantity=qty, entry_price=entry_price,
        )

    def exit(self, leg: LegFill, quote: dict | None = None) -> None:
        self._place(leg.tradingsymbol, leg.symboltoken, leg.exchange, "SELL", leg.quantity, quote)
