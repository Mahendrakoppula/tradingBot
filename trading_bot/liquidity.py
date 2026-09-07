import logging

from trading_bot.rest_client import RestClient

log = logging.getLogger(__name__)


def get_quote_for_contract(rest: RestClient, exchange: str, token: str) -> dict | None:
    """FULL-mode quote (has depth + open interest) for a single contract."""
    data = rest.get_quote("FULL", {exchange: [token]})
    fetched = data.get("fetched", [])
    return fetched[0] if fetched else None


def check_liquidity(quote: dict, max_spread_pct: float, min_oi: int) -> tuple[bool, str]:
    """Before firing an order on this contract - is there real depth to
    trade against, and is the spread tight enough that a market/limit
    order won't fill at a much worse price than the LTP the entry decision
    was based on? First-cut thresholds, not backtested."""
    depth = quote.get("depth", {}) or {}
    buy_levels = depth.get("buy", []) or []
    sell_levels = depth.get("sell", []) or []
    best_bid = float(buy_levels[0]["price"]) if buy_levels and float(buy_levels[0].get("quantity", 0)) > 0 else 0.0
    best_ask = float(sell_levels[0]["price"]) if sell_levels and float(sell_levels[0].get("quantity", 0)) > 0 else 0.0
    oi = int(float(quote.get("opnInterest", 0) or 0))

    if best_ask <= 0:
        return False, "no ask-side depth available (illiquid contract)"
    if oi < min_oi:
        return False, f"open interest {oi} below minimum {min_oi}"
    if best_bid > 0:
        spread_pct = (best_ask - best_bid) / best_ask * 100
        if spread_pct > max_spread_pct:
            return False, f"bid-ask spread {spread_pct:.1f}% exceeds max {max_spread_pct}%"
    return True, f"OI={oi}, best bid/ask={best_bid}/{best_ask}"


def entry_limit_price(quote: dict, buffer_pct: float) -> float:
    """Best ask + a small buffer - likely to fill immediately (buying
    slightly above the current best offer) while still capping worst-case
    slippage, unlike an unbounded MARKET order."""
    sell_levels = (quote.get("depth", {}) or {}).get("sell", []) or []
    best_ask = float(sell_levels[0]["price"]) if sell_levels and float(sell_levels[0].get("quantity", 0)) > 0 else float(quote["ltp"])
    return round(best_ask * (1 + buffer_pct / 100), 2)


def exit_limit_price(quote: dict, buffer_pct: float) -> float:
    """Best bid - a small buffer - likely to fill immediately (selling
    slightly below the current best bid) while capping worst-case slippage."""
    buy_levels = (quote.get("depth", {}) or {}).get("buy", []) or []
    best_bid = float(buy_levels[0]["price"]) if buy_levels and float(buy_levels[0].get("quantity", 0)) > 0 else float(quote["ltp"])
    return round(best_bid * (1 - buffer_pct / 100), 2)
