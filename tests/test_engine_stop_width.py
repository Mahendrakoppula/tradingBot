"""Stop width is a sizing input, not a veto.

Measured 2026-10-15. `max_stop_atr` was 2.5 and hardcoded, and it rejected the
two highest-scoring signals in the engine's history, both at STOP_PLAN, before
sizing or expected value could price them:

    2026-10-08 09:35  NIFTY   PDH_PDL_TRAP  score 72  3.43 ATR > 2.5
    2026-10-08 09:35  SENSEX  PDH_PDL_TRAP  score 64  4.23 ATR > 2.5

Those are the top two scores ever journaled; the next best is 56.

A wide stop is not itself a risk. Sizing (spec 27) turns stop width into
QUANTITY, and the binding question is whether one lot fits the risk budget.
That is instrument-specific, while an ATR cap is instrument-blind:

    budget Rs.6,000 (capital 200k x 3%), delta ~0.45
      NIFTY      lot 65, ATR 28.4 -> 205 pts = 7.22 ATR
      BANKNIFTY  lot 35, ATR 85.0 -> 381 pts = 4.48 ATR
      SENSEX     lot 20, ATR 95.0 -> 667 pts = 7.02 ATR

So 2.5 sat 2-3x below what the budget allowed, and pre-empted the two gates
that measure it exactly: one_lot_exceeds_max_risk and expected value.

What survives is a sanity bound, taken from measured data rather than taste:
half the measured median full-day range (daily_range_atr_cap = 10.0) = 5.0 ATR.
An invalidation further away than half a typical day's entire range is not a
structural intraday stop.

NOTE what this does NOT claim. Admitting these setups is not predicting they
trade. The NIFTY case had target 22475.85 against entry 22504.60 and a 97.5
point stop - R:R 0.29, which EV should and does refuse. The point is that the
refusal now comes from the gate that prices the trade, not from a proxy.
"""
import datetime as dt

import pytest

from trading_bot.engine.context import ContextSnapshot
from trading_bot.engine.stops import PlanRejected, StopParams, build_plan
from trading_bot.engine.strategies.base import Candidate

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

# The live NIFTY PDH_PDL_TRAP of 2026-10-08 09:35, read off its journal row:
# score 72, swept pdl @ 22546.30, direction down, rejected "3.43 ATR > 2.5".
SPOT, ATR = 22504.6, 28.42083426821262
STOP_ATR = 3.43
TARGET = 22475.85            # the candidate's own target - only 1.01 ATR away
OPTION_ENTRY, DELTA, GAMMA = 118.0, 0.45, 0.0016

OLD = StopParams(max_stop_atr=2.5)
NEW = StopParams()


def _ctx():
    return ContextSnapshot(
        ts=dt.datetime(2026, 10, 8, 9, 35, tzinfo=IST), underlying="NIFTY", trigger_tf="5m",
        spot=SPOT, session_phase="09:15-10:00", quality="OK", bar_index=4,
        trends={"5m": {"label": "STRONG_BEAR", "score": -0.77, "exhaustion": False}}, alignment={},
        regime={"primary": "HIGH_VOLATILITY"}, structure={}, levels={},
        price_action={}, indicators={"atr": ATR}, volume={})


def _cand(stop_atr=STOP_ATR, target=TARGET):
    return Candidate(strategy="PDH_PDL_TRAP", version="v0.1", underlying="NIFTY", direction="down",
                     entry_ref=SPOT, invalidation=SPOT + stop_atr * ATR, target_ref=target,
                     confirmation="swept_pdl_closed_back_inside", counter_trend=False)


def _plan(cand, params):
    return build_plan(_ctx(), cand, option_entry=OPTION_ENTRY, delta=DELTA, gamma=GAMMA, params=params)


class TestTheHistoricalRejections:
    def test_the_old_cap_reproduces_the_journal(self):
        r = _plan(_cand(), OLD)
        assert isinstance(r, PlanRejected)
        assert r.reason_code == "stop_too_wide" and "3.43 ATR > 2.5" in r.detail

    def test_the_new_bound_lets_it_through_to_be_priced(self):
        plan = _plan(_cand(), NEW)
        assert not isinstance(plan, PlanRejected), plan
        assert plan.stop_atr == pytest.approx(STOP_ATR, abs=0.01)

    def test_the_sensex_case_at_4_23_atr_also_passes(self):
        plan = _plan(_cand(stop_atr=4.23), NEW)
        assert not isinstance(plan, PlanRejected)

    def test_being_admitted_is_not_being_approved(self):
        """The NIFTY setup's own target was 1.01 ATR against a 3.43 ATR stop. It
        should still be refused - by EV, on R:R 0.29, which is the gate that
        actually prices it."""
        plan = _plan(_cand(), NEW)
        assert plan.rr1 < 0.5, f"R:R {plan.rr1:.2f} - this trade is still bad, just bad for the right reason"


class TestTheBoundIsStillAnOuterBound:
    def test_the_shipped_value_is_half_the_measured_daily_range(self):
        from trading_bot.engine.expected_move import MoveParams
        assert NEW.max_stop_atr == 5.0 == MoveParams().daily_range_atr_cap / 2.0

    @pytest.mark.parametrize("stop_atr", [5.5, 7.0, 12.0])
    def test_a_stop_beyond_the_bound_is_still_refused(self, stop_atr):
        """A nonsense invalidation must still be caught - that is all the cap is for."""
        r = _plan(_cand(stop_atr=stop_atr), NEW)
        assert isinstance(r, PlanRejected) and r.reason_code == "stop_too_wide"

    def test_a_stop_is_never_shrunk_to_fit(self):
        """Spec 26: the stop is market-driven. Widening the bound must not start
        tightening stops to make them fit."""
        plan = _plan(_cand(stop_atr=4.0), NEW)
        assert plan.stop_ref == pytest.approx(SPOT + 4.0 * ATR, abs=0.5)

    def test_the_minimum_still_widens_a_noise_tight_stop(self):
        plan = _plan(_cand(stop_atr=0.05), NEW)
        assert plan.stop_atr == pytest.approx(NEW.min_stop_atr, abs=0.01)
        assert any("minimum" in n for n in plan.notes)


class TestTheBudgetIsWhatActuallyBinds:
    @pytest.mark.parametrize("name,lot,atr,expected_atr", [
        ("NIFTY", 65, 28.4, 7.22), ("BANKNIFTY", 35, 85.0, 4.48), ("SENSEX", 20, 95.0, 7.02)])
    def test_the_budget_implied_bound_varies_by_instrument(self, name, lot, atr, expected_atr):
        """Why a single ATR cap is the wrong tool: the real limit is 4.5-7.2 ATR
        depending on the instrument, and only sizing can know that."""
        budget = 200_000 * 0.03
        assert budget / (lot * 0.45) / atr == pytest.approx(expected_atr, abs=0.02)

    def test_the_sanity_bound_does_not_exceed_every_budget(self):
        """5.0 ATR is above BANKNIFTY's budget-implied 4.48, so for BANKNIFTY
        sizing binds first - which is the intended division of labour."""
        assert NEW.max_stop_atr > 200_000 * 0.03 / (35 * 0.45) / 85.0


def test_config_carries_the_stop_bounds(monkeypatch):
    """It was hardcoded, which is how a 2.5 cap survived untunable long enough to
    reject the two best signals the engine ever produced."""
    from trading_bot.engine.config import EngineConfig
    from trading_bot.engine.pipeline import PipelineParams
    cfg = EngineConfig.from_env()
    assert cfg.max_stop_atr == 5.0 and cfg.min_stop_atr == 0.3
    p = PipelineParams.from_config(cfg)
    assert p.stops.max_stop_atr == 5.0 and p.stops.min_stop_atr == 0.3
    monkeypatch.setenv("TECH_MAX_STOP_ATR", "2.5")
    assert PipelineParams.from_config(EngineConfig.from_env()).stops.max_stop_atr == 2.5
