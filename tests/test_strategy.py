import datetime as dt

from trading_bot.options import OptionContract
from trading_bot.strategy import CondorLegs, IronCondorStrategy, place_split_order


class FakeRest:
    def __init__(self):
        self.placed = []

    def place_order(self, order):
        self.placed.append(order)
        return {"dry_run": True, "order": order}

    def get_ltp(self, exchange, tradingsymbol, symboltoken):
        return {"ltp": "123.45"}


def _contract(sym, ot):
    return OptionContract(
        token="1", tradingsymbol=sym, name="X", expiry=dt.date.today(),
        strike=100, option_type=ot, lotsize=1, freeze_qty=1000, exchange="NFO",
    )


def test_place_split_order_splits_above_freeze_qty():
    rest = FakeRest()
    place_split_order(rest, "TESTOPT", "111", "NFO", "SELL", total_qty=200, freeze_qty=90)
    qtys = [int(o["quantity"]) for o in rest.placed]
    assert qtys == [90, 90, 20]
    assert sum(qtys) == 200


def test_place_split_order_single_order_below_freeze_qty():
    rest = FakeRest()
    place_split_order(rest, "TESTOPT", "111", "NFO", "SELL", total_qty=65, freeze_qty=1801)
    qtys = [int(o["quantity"]) for o in rest.placed]
    assert qtys == [65]


def test_condor_entry_buys_hedges_before_shorts():
    rest = FakeRest()
    strat = IronCondorStrategy(rest)
    legs = CondorLegs(
        short_call=_contract("SC", "CE"), short_put=_contract("SP", "PE"),
        hedge_call=_contract("HC", "CE"), hedge_put=_contract("HP", "PE"),
    )
    strat.enter(legs, qty_lots=1)
    order_sequence = [o["tradingsymbol"] + ":" + o["transactiontype"] for o in rest.placed]
    assert order_sequence == ["HC:BUY", "HP:BUY", "SC:SELL", "SP:SELL"]


def test_condor_exit_closes_shorts_before_hedges():
    rest = FakeRest()
    strat = IronCondorStrategy(rest)
    legs = CondorLegs(
        short_call=_contract("SC", "CE"), short_put=_contract("SP", "PE"),
        hedge_call=_contract("HC", "CE"), hedge_put=_contract("HP", "PE"),
    )
    fills = strat.enter(legs, qty_lots=1)
    rest.placed.clear()
    strat.exit(fills)
    exit_sequence = [o["tradingsymbol"] + ":" + o["transactiontype"] for o in rest.placed]
    assert exit_sequence == ["SC:BUY", "SP:BUY", "HC:SELL", "HP:SELL"]
