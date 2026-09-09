import logging

from trading_bot.options import OptionContract
from trading_bot.rest_client import RestClient
from trading_bot.strategy import CondorLegs

log = logging.getLogger(__name__)


def size_long_option(rest: RestClient, contract: OptionContract, budget: float, max_lots: int) -> tuple[int, float]:
    """How many lots `budget` rupees can buy of this option, at its current
    LTP. No margin call needed here (unlike size_condor) - buying an option
    only ever costs the premium, so the premium itself IS the capital
    requirement. Returns (lots, premium_per_lot).
    """
    ltp_data = rest.get_ltp(contract.exchange, contract.tradingsymbol, contract.token)
    premium_per_lot = float(ltp_data["ltp"]) * contract.lotsize
    if premium_per_lot <= 0:
        log.warning("Non-positive premium (%.2f) for %s - skipping entry", premium_per_lot, contract.tradingsymbol)
        return 0, premium_per_lot

    lots = min(int(budget // premium_per_lot), max_lots)
    log.info("Premium per lot: Rs.%.2f, budget: Rs.%.2f -> sizing %d lot(s)", premium_per_lot, budget, lots)
    return lots, premium_per_lot


def size_equity_shares(price: float, budget: float) -> int:
    """Whole shares `budget` rupees can buy at `price` - no lot size, no
    margin call (CNC delivery is paid for in full, unlike F&O)."""
    if price <= 0:
        return 0
    return int(budget // price)


def margin_positions_for_condor(legs: CondorLegs, qty_lots: int = 1) -> list[dict]:
    def pos(contract, trade_type: str) -> dict:
        return {
            "exchange": contract.exchange,
            "qty": contract.lotsize * qty_lots,
            "price": 0,
            "productType": "INTRADAY",
            "token": contract.token,
            "tradeType": trade_type,
            "orderType": "MARKET",
        }

    # Hedge legs first so the margin engine sees the protective legs before
    # the shorts - matches the actual entry order and gets the hedge benefit
    # applied to the margin estimate rather than pricing the shorts naked.
    return [
        pos(legs.hedge_call, "BUY"),
        pos(legs.hedge_put, "BUY"),
        pos(legs.short_call, "SELL"),
        pos(legs.short_put, "SELL"),
    ]


def size_condor(rest: RestClient, legs: CondorLegs, budget: float, max_lots: int) -> tuple[int, float]:
    """How many lots `budget` rupees of margin can support for this condor,
    based on the broker's own margin estimate for one lot. Returns
    (lots, margin_per_lot). lots is 0 if even one lot doesn't fit the budget.
    """
    margin_data = rest.get_margin(margin_positions_for_condor(legs, qty_lots=1))
    margin_per_lot = float(margin_data["totalMarginRequired"])
    if margin_per_lot <= 0:
        log.warning("Margin calculator returned non-positive margin (%.2f) - skipping entry", margin_per_lot)
        return 0, margin_per_lot

    lots = min(int(budget // margin_per_lot), max_lots)
    log.info("Margin per lot: Rs.%.2f, budget: Rs.%.2f -> sizing %d lot(s)", margin_per_lot, budget, lots)
    return lots, margin_per_lot
