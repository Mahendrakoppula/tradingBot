import datetime as dt
import tempfile
from pathlib import Path

import trading_bot.state as state_mod
from trading_bot.debit_strategy import LongOptionStrategy, pick_direction, pick_momentum_direction
from trading_bot.options import OptionContract


def test_pick_direction_long_built_up_means_buy_calls():
    direction, reason = pick_direction({"Long Built Up": [{"tradingSymbol": "NIFTY28DEC26FUT"}]}, "NIFTY")
    assert direction == "CE"


def test_pick_direction_short_built_up_means_buy_puts():
    direction, reason = pick_direction({"Short Built Up": [{"tradingSymbol": "NIFTY28DEC26FUT"}]}, "NIFTY")
    assert direction == "PE"


def test_pick_direction_no_signal_returns_none():
    direction, reason = pick_direction({}, "NIFTY")
    assert direction is None


def test_momentum_up_past_threshold_means_buy_calls():
    direction, reason = pick_momentum_direction({"open": 100.0, "ltp": 100.20}, min_move_pct=0.15)
    assert direction == "CE"


def test_momentum_down_past_threshold_means_buy_puts():
    direction, reason = pick_momentum_direction({"open": 100.0, "ltp": 99.80}, min_move_pct=0.15)
    assert direction == "PE"


def test_momentum_inside_band_means_no_signal():
    direction, reason = pick_momentum_direction({"open": 100.0, "ltp": 100.05}, min_move_pct=0.15)
    assert direction is None


def test_momentum_missing_open_does_not_crash():
    direction, reason = pick_momentum_direction({"open": 0, "ltp": 100.0}, min_move_pct=0.15)
    assert direction is None


class FakeRest:
    def __init__(self, ltp=100.0):
        self.ltp = ltp
        self.placed = []

    def get_ltp(self, exchange, tradingsymbol, symboltoken):
        return {"ltp": str(self.ltp)}

    def place_order(self, order):
        self.placed.append(order)
        return {"dry_run": True, "order": order}


def _contract(sym, ot, lotsize=65):
    return OptionContract(
        token="1", tradingsymbol=sym, name="NIFTY", expiry=dt.date.today(),
        strike=100, option_type=ot, lotsize=lotsize, freeze_qty=1801, exchange="NFO",
    )


def test_long_option_strategy_enter_buys_and_exit_sells():
    rest = FakeRest(ltp=20.0)
    strat = LongOptionStrategy(rest)
    contract = _contract("NIFTY08SEP2624000CE", "CE")

    leg = strat.enter(contract, qty_lots=2)
    assert leg.transaction_type == "BUY"
    assert leg.quantity == 130
    assert rest.placed[0]["transactiontype"] == "BUY"

    rest.placed.clear()
    strat.exit(leg)
    assert rest.placed[0]["transactiontype"] == "SELL"
    assert rest.placed[0]["quantity"] == "130"


def test_long_option_strategy_uses_limit_order_when_quote_given():
    rest = FakeRest(ltp=20.0)
    strat = LongOptionStrategy(rest, limit_buffer_pct=0.5)
    contract = _contract("NIFTY08SEP2624000CE", "CE")
    quote = {"ltp": 20.0, "depth": {"buy": [{"price": 19.5, "quantity": 100}], "sell": [{"price": 20.5, "quantity": 100}]}}

    leg = strat.enter(contract, qty_lots=1, quote=quote)
    assert rest.placed[0]["ordertype"] == "LIMIT"
    assert rest.placed[0]["price"] == "20.6"  # 20.5 * 1.005 rounded
    assert leg.entry_price == 20.6

    rest.placed.clear()
    strat.exit(leg, quote=quote)
    assert rest.placed[0]["ordertype"] == "LIMIT"
    assert rest.placed[0]["price"] == "19.4"  # 19.5 * 0.995 rounded


def test_long_option_state_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(state_mod, "LONG_STATE_PATH", tmp_path / "long_positions.json")
    rest = FakeRest(ltp=20.0)
    strat = LongOptionStrategy(rest)
    contract = _contract("NIFTY08SEP2624000CE", "CE")
    leg = strat.enter(contract, qty_lots=1)

    position = state_mod.OpenLongOption(underlying="NIFTY", expiry="08SEP2026", entered_at="2026-09-07T12:30:00", option=leg)
    state_mod.save_long({"NIFTY": position})
    loaded = state_mod.load_long()
    assert loaded["NIFTY"].option.tradingsymbol == contract.tradingsymbol
    assert loaded["NIFTY"].option.quantity == 65
