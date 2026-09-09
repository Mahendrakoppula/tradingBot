from trading_bot.equity_strategy import EquityDeliveryStrategy


class FakeRest:
    def __init__(self, ltp=100.0):
        self.ltp = ltp
        self.placed = []

    def place_order(self, order):
        self.placed.append(order)
        return {"dry_run": True, "order": order}

    def get_ltp(self, exchange, tradingsymbol, symboltoken):
        return {"ltp": str(self.ltp)}


def test_enter_buys_market_order_without_quote():
    rest = FakeRest(ltp=250.0)
    strat = EquityDeliveryStrategy(rest)
    leg = strat.enter("RELIANCE-EQ", "1", "NSE", qty=10)
    assert rest.placed[0]["transactiontype"] == "BUY"
    assert rest.placed[0]["producttype"] == "DELIVERY"
    assert rest.placed[0]["ordertype"] == "MARKET"
    assert rest.placed[0]["quantity"] == "10"
    assert leg.entry_price == 250.0
    assert leg.lotsize == 1
    assert leg.freeze_qty == 0
    assert leg.quantity == 10


def test_enter_uses_limit_order_when_quote_given():
    rest = FakeRest(ltp=200.0)
    strat = EquityDeliveryStrategy(rest, limit_buffer_pct=0.5)
    quote = {"ltp": 200.0, "depth": {"buy": [{"price": 198.0, "quantity": 100}], "sell": [{"price": 200.0, "quantity": 100}]}}
    leg = strat.enter("RELIANCE-EQ", "1", "NSE", qty=10, quote=quote)
    assert rest.placed[0]["ordertype"] == "LIMIT"
    assert rest.placed[0]["price"] == "201.0"  # 200 * 1.005
    assert leg.entry_price == 201.0


def test_exit_sells_full_quantity_at_bid_side_limit():
    rest = FakeRest(ltp=200.0)
    strat = EquityDeliveryStrategy(rest, limit_buffer_pct=0.5)
    quote = {"ltp": 200.0, "depth": {"buy": [{"price": 198.0, "quantity": 100}], "sell": [{"price": 200.0, "quantity": 100}]}}
    leg = strat.enter("RELIANCE-EQ", "1", "NSE", qty=10, quote=quote)
    rest.placed.clear()
    strat.exit(leg, quote=quote)
    assert rest.placed[0]["transactiontype"] == "SELL"
    assert rest.placed[0]["ordertype"] == "LIMIT"
    assert rest.placed[0]["price"] == "197.01"  # 198 * 0.995
    assert rest.placed[0]["quantity"] == "10"


def test_exit_falls_back_to_market_order_without_quote():
    rest = FakeRest(ltp=200.0)
    strat = EquityDeliveryStrategy(rest)
    leg = strat.enter("RELIANCE-EQ", "1", "NSE", qty=10)
    rest.placed.clear()
    strat.exit(leg)
    assert rest.placed[0]["ordertype"] == "MARKET"
    assert "price" not in rest.placed[0]
