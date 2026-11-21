"""A fill must not be priced at a spot the engine has already moved past.

Measured 2026-11-11 on the only two closed trades the engine has produced.
Both captured only ~30% of delta x underlying-move, against a median of 0.95
over 311,636 real chain rows - so the defect was never the delta/gamma model,
it was WHICH quote the fill used.

The mechanism, from the 21 Oct BANKNIFTY 55300PE stop:

    chain refresh cadence  TECH_CHAIN_REFRESH_SECONDS = 60s
    exit trigger cadence   1m index bars (_on_bar replaces ctx with bar.close)

    10:25 chain      bid 347.00  ask 347.90  spot 55404.10  delta -0.4328
    10:26 1m bar     index closes 55455.75   (+52 points inside one minute)
    10:27:01 exit    STOP_LOSS fires on that bar, fills at 347.02
    10:30 chain      bid 332.15                spot 55460.65

So the stop fired on a fresh bar and was filled at a quote taken before the
move that fired it. Booked loss 6.33/unit; delta implied 20.9/unit, and the
next observed chain agrees. Understated the loss ~3.3x.

The effect is symmetric and therefore worse than a simple bias: it shrinks
losses AND wins toward zero, so the one paper record this engine has is
compressed. Any expected-value work built on it would fit the compression.
"""
import dataclasses
import datetime as dt

import pytest

from trading_bot.engine.option_chain import OptionQuote
from trading_bot.options import OptionContract

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

# The live 21 Oct quote, read off option_chain_snapshots at 10:25.
SPOT_AT_QUOTE = 55404.10
SPOT_AT_TRIGGER = 55455.75          # the 10:26 1m index close that fired the stop
BID, ASK, LTP = 347.00, 347.90, 347.80
DELTA, GAMMA = -0.4328, 0.0000176   # PE: delta negative, gamma positive


def _contract():
    return OptionContract(token="48321", tradingsymbol="BANKNIFTY27OCT2655300PE", name="BANKNIFTY",
                          expiry=dt.date(2026, 10, 27), strike=55300.0, option_type="PE",
                          lotsize=35, freeze_qty=900, exchange="NFO")


def _quote(**over) -> OptionQuote:
    q = OptionQuote(contract=_contract(), ts=dt.datetime(2026, 10, 21, 10, 25, tzinfo=IST),
                    ltp=LTP, bid=BID, ask=ASK, bid_qty=100, ask_qty=100, volume=1000, oi=5000,
                    iv=0.1486, delta=DELTA, gamma=GAMMA, theta_per_day=-8.0, vega=12.0,
                    greeks_source="broker", spot=SPOT_AT_QUOTE)
    return dataclasses.replace(q, **over) if over else q


class TestTheLiveStop:
    def test_the_stale_quote_is_what_booked_6_rupees_of_loss(self):
        """Entry was 353.35. Filling at the unadvanced bid books ~6/unit."""
        assert 353.35 - _quote().bid == pytest.approx(6.35, abs=0.1)

    def test_advancing_to_the_trigger_bar_recovers_the_real_loss(self):
        adv = _quote().advanced_to(SPOT_AT_TRIGGER)
        # delta*ds + 0.5*gamma*ds^2 over +51.65 points, against a -0.4328 delta
        assert adv.bid == pytest.approx(324.6, abs=1.0)
        booked = 353.35 - adv.bid
        assert booked == pytest.approx(28.7, abs=1.0)
        # and it is in the ballpark the NEXT observed chain confirms (332.15 bid
        # at spot 55460.65), rather than the 347 the stale quote claimed
        assert abs(adv.bid - 332.15) < abs(_quote().bid - 332.15)

    def test_the_direction_is_a_loss_for_a_put_when_the_index_rises(self):
        assert _quote().advanced_to(SPOT_AT_TRIGGER).bid < BID

    def test_a_call_gains_when_the_index_rises(self):
        ce = _quote(delta=0.504)
        assert ce.advanced_to(SPOT_AT_TRIGGER).bid > BID


    def test_the_correction_is_an_improvement_not_an_oracle(self):
        """Honest bound on what this buys.

        The next observed chain (10:30, spot 55460.65) had the put at 332.15,
        i.e. a real loss of ~21.2/unit from the 353.35 entry - which is what
        delta implied over the whole move. Advancing the 10:25 quote lands at
        ~324.7, so it OVERSHOOTS the loss by ~7 points, because the expansion
        cannot see IV (0.1486 -> 0.1509 over those minutes) or theta.

        That is still a large improvement on 347.02, and the direction of the
        residual is conservative for a stop. It is not a claim of exactness,
        and a replay that needs the true figure should read the next observed
        chain rather than trust this.
        """
        adv = _quote().advanced_to(SPOT_AT_TRIGGER)
        observed_next_chain = 332.15
        stale_err = abs(BID - observed_next_chain)
        adv_err = abs(adv.bid - observed_next_chain)
        assert adv_err < stale_err / 1.5, f"stale off by {stale_err:.2f}, advanced off by {adv_err:.2f}"
        assert adv.bid < observed_next_chain, "residual should err toward the conservative side for a stop"


class TestTheExpansionItself:
    def test_the_spread_is_carried_across_unchanged(self):
        """Spread is a liquidity property of the strike, not a function of spot."""
        q = _quote()
        adv = q.advanced_to(SPOT_AT_TRIGGER)
        assert adv.ask - adv.bid == pytest.approx(q.ask - q.bid, abs=0.02)

    def test_gamma_helps_a_long_in_both_directions(self):
        """Convexity is positive for a long call AND a long put."""
        q = _quote()
        lin_down = q.bid + q.delta * (SPOT_AT_TRIGGER - SPOT_AT_QUOTE)
        assert q.advanced_to(SPOT_AT_TRIGGER).bid > lin_down

    def test_spot_is_restamped_so_a_second_advance_is_not_double_counted(self):
        adv = _quote().advanced_to(SPOT_AT_TRIGGER)
        assert adv.spot == SPOT_AT_TRIGGER
        assert adv.advanced_to(SPOT_AT_TRIGGER) is adv

    def test_a_premium_cannot_go_negative(self):
        assert _quote().advanced_to(SPOT_AT_QUOTE + 100_000).bid >= 0.05

    @pytest.mark.parametrize("missing", [{"delta": None}, {"spot": None}])
    def test_a_missing_input_degrades_to_the_old_behaviour(self, missing):
        """No greek, no guess: return the quote untouched rather than invent one."""
        q = _quote(**missing)
        assert q.advanced_to(SPOT_AT_TRIGGER) is q

    def test_no_move_is_a_no_op(self):
        q = _quote()
        assert q.advanced_to(SPOT_AT_QUOTE) is q
        assert q.advanced_to(None) is q

    def test_a_zero_bid_stays_zero(self):
        """An absent bid is absent, not a tradable price shifted by delta."""
        assert _quote(bid=0.0).advanced_to(SPOT_AT_TRIGGER).bid == 0.0


def test_config_carries_the_switch(monkeypatch):
    """Kept switchable so the historical cost can be measured by replaying
    both ways, rather than asserted."""
    from trading_bot.engine.config import EngineConfig
    assert EngineConfig.from_env().fill_sync_to_spot is True
    monkeypatch.setenv("TECH_FILL_SYNC_TO_SPOT", "false")
    assert EngineConfig.from_env().fill_sync_to_spot is False
