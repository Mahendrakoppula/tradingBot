import datetime as dt

from trading_bot.options import OptionContract
from trading_bot.strategy import CondorLegs
from trading_bot.sizing import size_condor, size_equity_shares, size_long_option


def _contract(sym, ot, lotsize=65):
    return OptionContract(
        token="1", tradingsymbol=sym, name="NIFTY", expiry=dt.date.today(),
        strike=100, option_type=ot, lotsize=lotsize, freeze_qty=1801, exchange="NFO",
    )


class FakeMarginRest:
    def __init__(self, margin_per_lot):
        self.margin_per_lot = margin_per_lot

    def get_margin(self, positions):
        return {"totalMarginRequired": self.margin_per_lot}


def test_size_condor_basic():
    legs = CondorLegs(
        short_call=_contract("SC", "CE"), short_put=_contract("SP", "PE"),
        hedge_call=_contract("HC", "CE"), hedge_put=_contract("HP", "PE"),
    )
    rest = FakeMarginRest(margin_per_lot=6000)
    lots, margin = size_condor(rest, legs, budget=15000, max_lots=5)
    assert lots == 2
    assert margin == 6000.0


def test_size_condor_skips_when_budget_too_small():
    legs = CondorLegs(
        short_call=_contract("SC", "CE"), short_put=_contract("SP", "PE"),
        hedge_call=_contract("HC", "CE"), hedge_put=_contract("HP", "PE"),
    )
    rest = FakeMarginRest(margin_per_lot=6000)
    lots, margin = size_condor(rest, legs, budget=3000, max_lots=5)
    assert lots == 0


def test_size_condor_respects_max_lots_ceiling():
    legs = CondorLegs(
        short_call=_contract("SC", "CE"), short_put=_contract("SP", "PE"),
        hedge_call=_contract("HC", "CE"), hedge_put=_contract("HP", "PE"),
    )
    rest = FakeMarginRest(margin_per_lot=100)
    lots, margin = size_condor(rest, legs, budget=15000, max_lots=3)
    assert lots == 3  # would be 150 by budget alone, capped to 3


class FakeLtpRest:
    def __init__(self, ltp):
        self.ltp = ltp

    def get_ltp(self, exchange, tradingsymbol, symboltoken):
        return {"ltp": str(self.ltp)}


def test_size_long_option_basic():
    contract = _contract("NIFTY08SEP2624000CE", "CE", lotsize=65)
    rest = FakeLtpRest(ltp=20.0)  # premium 20 * 65 = 1300/lot
    lots, premium = size_long_option(rest, contract, budget=3000, max_lots=5)
    assert premium == 1300.0
    assert lots == 2  # floor(3000/1300)


def test_size_long_option_skips_when_budget_too_small():
    contract = _contract("NIFTY08SEP2624000CE", "CE", lotsize=65)
    rest = FakeLtpRest(ltp=20.0)
    lots, premium = size_long_option(rest, contract, budget=500, max_lots=5)
    assert lots == 0


def test_size_equity_shares_basic():
    assert size_equity_shares(price=250.0, budget=3000.0) == 12  # floor(3000/250)


def test_size_equity_shares_zero_when_price_nonpositive():
    assert size_equity_shares(price=0.0, budget=3000.0) == 0
    assert size_equity_shares(price=-5.0, budget=3000.0) == 0


def test_size_equity_shares_zero_when_budget_too_small():
    assert size_equity_shares(price=500.0, budget=100.0) == 0
