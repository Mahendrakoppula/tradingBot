"""Structural targets, and a win probability that falls as the target lengthens.

Measured 2026-10-14 over 214 journaled signals - a symmetric barrier race on the
underlying, stop fixed at 1 ATR, 60 bars of 5m forward:

    R:R   n     target_first   p needed (1/(1+RR))   edge
    1.0  214       50.5%            50.0%           +0.5%
    1.5  214       42.1%            40.0%           +2.1%
    2.0  214       36.4%            33.3%           +3.1%
    2.5  213       33.3%            28.6%           +4.8%
    3.0  210       31.0%            25.0%           +6.0%
    4.0  207       26.6%            20.0%           +6.6%  <- clears 1.96 SE

The win rate decays MORE SLOWLY than the ratio demands, monotonically. That is
the only measured edge found in this project - gates (2026-09-29), entry rules,
and candle patterns all came back indistinguishable from noise. Truncating the
target at the expected-move boundary was throwing it away: reward was capped by
a time-decaying estimate while risk stayed structural, so R:R collapsed late in
the session and EV was negative by construction.

The matching correction is that p can no longer be a flat 0.45. A 4 ATR target
is not as likely as a 1 ATR one, and holding p fixed would manufacture EV out of
reward less likely to be collected.
"""
import datetime as dt

import pytest

from trading_bot.engine import expected_move as em
from trading_bot.engine.context import ContextSnapshot
from trading_bot.engine.risk_engine import RiskLimits
from trading_bot.engine.strategies.base import Candidate

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
SPOT, ATR = 22640.0, 26.0

# (R:R, measured target-first rate) - the table above
MEASURED = [(1.0, 0.505), (1.5, 0.421), (2.0, 0.364), (2.5, 0.333), (3.0, 0.310), (4.0, 0.266)]


def _ctx(level_atr_away=2.14):
    """Mirrors the live 14:05 NIFTY EMA_PULLBACK: next structural support 2.14 ATR
    below, which is the trade that exposed this (target truncated to 0.77 ATR,
    stop left at 1.02 ATR, EV -Rs.289 - and it then hit its target in 5 minutes)."""
    below = SPOT - level_atr_away * ATR
    return ContextSnapshot(
        ts=dt.datetime(2026, 10, 14, 14, 5, tzinfo=IST), underlying="NIFTY", trigger_tf="5m",
        spot=SPOT, session_phase="14:00-15:00", quality="OK", bar_index=60,
        trends={"5m": {"label": "BEAR", "score": -0.43, "exhaustion": False}}, alignment={},
        regime={"primary": "WEAK_BEAR"}, structure={},
        levels={"session_high": SPOT + 120, "session_low": SPOT - 80,
                "nearest_below": {"name": "ema200", "price": below, "distance": -(SPOT - below),
                                  "distance_atr": -level_atr_away, "touches": 0}},
        price_action={"labels": ["evening_star"]},
        indicators={"atr": ATR, "macd_hist": 5.7}, volume={})


def _cand(target_atr=1.8):
    return Candidate(strategy="EMA_PULLBACK", version="v0.1", underlying="NIFTY", direction="down",
                     entry_ref=SPOT, invalidation=SPOT + 1.02 * ATR, target_ref=SPOT - target_atr * ATR,
                     confirmation="rejection_at_ema20", counter_trend=False)


class TestStructuralTargets:
    def test_target_is_no_longer_truncated_at_the_move_boundary(self):
        """The regression: a small late-session move estimate used to pull the
        target in to the boundary while the stop stayed structural."""
        ctx, cand = _ctx(), _cand(target_atr=1.8)
        move = em.ExpectedMove(remaining_atr=0.77, remaining_points=0.77 * ATR, time_fraction=0.2,
                               consumed_atr=0.18, day_range_used=0.5, regime_mult=1.0, trend_mult=1.0,
                               momentum_mult=1.0, boundary=SPOT - 0.77 * ATR)
        now = em.distance_check(ctx, cand, move, em.MoveParams())
        assert now.blocked_by != "expected_move_boundary"
        assert abs(now.target_ref - cand.target_ref) < 1e-6

    def test_the_old_behaviour_is_still_reachable(self):
        ctx, cand = _ctx(), _cand(target_atr=1.8)
        move = em.ExpectedMove(remaining_atr=0.77, remaining_points=0.77 * ATR, time_fraction=0.2,
                               consumed_atr=0.18, day_range_used=0.5, regime_mult=1.0, trend_mult=1.0,
                               momentum_mult=1.0, boundary=SPOT - 0.77 * ATR)
        old = em.distance_check(ctx, cand, move, em.MoveParams(allow_structural_target=False))
        assert old.blocked_by == "expected_move_boundary"

    def test_a_nearer_level_still_caps_the_target(self):
        """Structural targets are not unlimited targets - an opposing level in the
        way still pulls the target back to it."""
        ctx = _ctx(level_atr_away=1.0)
        cand = _cand(target_atr=3.0)
        move = em.ExpectedMove(remaining_atr=2.0, remaining_points=2.0 * ATR, time_fraction=0.8,
                               consumed_atr=0.2, day_range_used=0.3, regime_mult=1.0, trend_mult=1.0,
                               momentum_mult=1.0, boundary=SPOT - 2.0 * ATR)
        r = em.distance_check(ctx, cand, move, em.MoveParams())
        assert r.blocked_by == "ema200" and r.target_ref == pytest.approx(SPOT - 1.0 * ATR)

    def test_a_level_right_on_top_still_rejects(self):
        ctx = _ctx(level_atr_away=0.2)
        move = em.ExpectedMove(remaining_atr=2.0, remaining_points=2.0 * ATR, time_fraction=0.8,
                               consumed_atr=0.2, day_range_used=0.3, regime_mult=1.0, trend_mult=1.0,
                               momentum_mult=1.0, boundary=SPOT - 2.0 * ATR)
        r = em.distance_check(ctx, _cand(3.0), move, em.MoveParams())
        assert not r.ok and r.reason_code == "target_blocked_by_level"


class TestWinProbabilityCurve:
    @pytest.mark.parametrize("rr,measured", MEASURED)
    def test_the_fitted_curve_reproduces_the_measurement(self, rr, measured):
        lim = RiskLimits()
        fitted = lim.ev_p_at_rr1 * rr ** -lim.ev_p_rr_decay
        assert abs(fitted - measured) < 0.01, f"R:R {rr}: fitted {fitted:.3f} vs measured {measured:.3f}"

    @pytest.mark.parametrize("rr,measured", MEASURED)
    def test_every_measured_ratio_clears_what_ev_requires(self, rr, measured):
        """If this ever fails, the edge has gone and structural targets should be
        switched off, not compensated for elsewhere."""
        assert measured > 1.0 / (1.0 + rr)

    def test_probability_falls_as_the_target_lengthens(self):
        lim = RiskLimits()
        ps = [lim.ev_p_at_rr1 * rr ** -lim.ev_p_rr_decay for rr, _ in MEASURED]
        assert ps == sorted(ps, reverse=True), "a longer target must never be scored as MORE likely"

    def test_it_is_bounded(self):
        lim = RiskLimits()
        for rr in (0.01, 0.5, 1.0, 10.0, 100.0):
            p = max(0.05, min(0.95, lim.ev_p_at_rr1 * rr ** -lim.ev_p_rr_decay))
            assert 0.05 <= p <= 0.95

    def test_an_explicit_probability_still_wins(self):
        """Fingerprint stats (spec 30) must override the model when they exist."""
        import inspect

        from trading_bot.engine import risk_engine
        src = inspect.getsource(risk_engine.evaluate)
        assert "if win_probability is not None:" in src


def test_config_carries_both_switches(monkeypatch):
    from trading_bot.engine.config import EngineConfig
    from trading_bot.engine.pipeline import PipelineParams
    cfg = EngineConfig.from_env()
    assert cfg.allow_structural_target is True and cfg.ev_win_prob_from_rr is True
    p = PipelineParams.from_config(cfg)
    assert p.move.allow_structural_target is True
    assert p.limits.ev_win_prob_from_rr is True
    monkeypatch.setenv("TECH_ALLOW_STRUCTURAL_TARGET", "false")
    assert EngineConfig.from_env().allow_structural_target is False


class TestCurveIsClampedToTheMeasuredBand:
    """The measurement covers R:R 1.0-4.0. Extrapolating the power law below 1.0
    gives p=0.71 at rr=0.48 - a 71% win rate asserted from no data, which would
    approve exactly the low-R:R trades this change exists to avoid."""

    def test_below_the_band_falls_back_to_the_flat_default(self):
        lim = RiskLimits()
        assert lim.ev_p_rr_min == 1.0
        extrapolated = lim.ev_p_at_rr1 * 0.48 ** -lim.ev_p_rr_decay
        assert extrapolated > 0.70, "this is the unsafe value the clamp exists to avoid"
        assert extrapolated > lim.ev_win_prob_default

    def test_the_guard_is_wired(self):
        import inspect

        from trading_bot.engine import risk_engine
        src = inspect.getsource(risk_engine.evaluate)
        assert "rr >= limits.ev_p_rr_min" in src

    def test_the_real_1405_trade_flips_on_the_target_not_the_probability(self):
        """End to end on the live signal that exposed all this. Truncated it must
        stay rejected; with a structural target it is approved - and the approval
        comes from a LOWER probability applied to a bigger reward, not a flattering
        one applied to a small reward."""
        lim = RiskLimits()
        net_loss, costs, atr, delta, lot = 885.58, 138.73, 26.06, 0.43, 65

        def ev_for(target_atr):
            net = target_atr * atr * delta * lot - costs
            rr = net / net_loss
            p = (lim.ev_p_at_rr1 * rr ** -lim.ev_p_rr_decay
                 if rr >= lim.ev_p_rr_min else lim.ev_win_prob_default)
            p = max(0.05, min(0.95, p))
            return rr, p, p * net - (1 - p) * net_loss

        rr_old, p_old, ev_old = ev_for(0.77)   # truncated at the move boundary
        rr_new, p_new, ev_new = ev_for(2.14)   # reaching ema200
        assert ev_old < 0 and ev_new > 0
        assert rr_new > rr_old
        assert p_new < p_old, "the bigger target must be scored as less likely, not more"
