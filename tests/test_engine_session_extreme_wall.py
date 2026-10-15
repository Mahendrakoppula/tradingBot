"""The session extreme is not a wall, and a breakout setup is not a chase.

Measured 2026-10-15 over the 212 journaled rejections. `estimate()` caps the
remaining move at the first level ahead and, for a "strong" level, gives ZERO
credit for continuing through it. session_high/session_low were classed strong
alongside pdh/pdl/pwh/pwl:

    14 of the 31 insufficient_remaining_move rejections (45%) had the session
    extreme as the level ahead, were truncated to exactly that distance
    (0.01-0.39 ATR), and were then refused for holding under 0.75 ATR of room.
    Ten of the fourteen still had 71-94% of the session left.

      2026-09-24  LEVEL_REJECTION       down  session_low   0.39 ATR  55% left
      2026-09-29  MOMENTUM_EXPANSION    down  session_low   0.30 ATR  93% left
      2026-10-06  VWAP_RECLAIM          up    session_high  0.37 ATR  94% left
      2026-10-06  COMPRESSION_BREAKOUT  up    session_high  0.07 ATR  89% left
      2026-10-07  ORB                   down  session_low   0.04 ATR  93% left
      2026-10-13  LEVEL_REJECTION       up    session_high  0.04 ATR  94% left
      2026-10-15  LEVEL_REJECTION       up    session_high  0.37 ATR  94% left

The defect is circular: a breakout or rejection setup fires AT the session
extreme by construction, so the level it exists to break is always the level
its room is measured to. The gate asking "is there room to move?" is answered
"no" by the very fact that makes the setup a setup.

A prior-day or prior-week extreme has a finished session of acceptance behind
it. An intraday extreme twenty minutes old does not, and the day's range
expands through it all session by definition. Hence the weak tier (40% credit
beyond), NOT removal from the cap - an extreme overhead is still resistance.
"""
import datetime as dt

import pytest

from trading_bot.engine import expected_move as em
from trading_bot.engine.context import ContextSnapshot
from trading_bot.engine.strategies.base import Candidate

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

# The live SENSEX LEVEL_REJECTION of 2026-10-15 09:35, read off its journal row and
# the matching context snapshot: swept or_high by 0.60 ATR, closed back inside,
# bullish close in direction, score 23, rejected insufficient_remaining_move
# "0.36 ATR < 0.75" with 94% of the session still to run.
SPOT, ATR = 73285.96, 96.41057133470744
SESSION_HIGH, SESSION_LOW = 73321.18, 72898.67


# How far the session extreme sat from spot on that bar. Kept separate from the
# session high/low below, which drive day_range_used - moving the LEVEL must not
# silently move the RANGE too, or a control stops being a control.
LEVEL_ATR = (SESSION_HIGH - SPOT) / ATR  # 0.365


def _ctx(direction="up", spot=SPOT, hi=SESSION_HIGH, lo=SESSION_LOW, name=None,
         level_atr=LEVEL_ATR, hh=9, mm=35, regime="HIGH_VOLATILITY", trend=None, macd=None):
    s = 1 if direction == "up" else -1
    if name is None:
        name = "session_high" if direction == "up" else "session_low"
    if trend is None:
        trend = "WEAK_BULL" if direction == "up" else "WEAK_BEAR"
    if macd is None:
        macd = s * 63.601
    level = spot + s * level_atr * ATR
    key = "nearest_above" if direction == "up" else "nearest_below"
    return ContextSnapshot(
        ts=dt.datetime(2026, 10, 15, hh, mm, tzinfo=IST), underlying="SENSEX", trigger_tf="5m",
        spot=spot, session_phase="09:15-10:00", quality="OK", bar_index=4,
        trends={"5m": {"label": trend, "score": s * 0.37, "exhaustion": False}}, alignment={},
        regime={"primary": regime}, structure={},
        levels={"session_high": hi, "session_low": lo,
                key: {"name": name, "price": level, "touches": 3,
                      "distance": level - spot, "distance_atr": (level - spot) / ATR}},
        price_action={}, indicators={"atr": ATR, "macd_hist": macd}, volume={})


def _cand(direction="up"):
    s = 1 if direction == "up" else -1
    return Candidate(strategy="LEVEL_REJECTION", version="v0.1", underlying="SENSEX",
                     direction=direction, entry_ref=SPOT, invalidation=SPOT - s * 0.16 * ATR,
                     target_ref=SPOT + s * 2.0 * ATR, confirmation="sweep_reclaim_of_or_high",
                     counter_trend=False)


OLD = em.MoveParams(session_extremes_are_strong=True)
NEW = em.MoveParams()


class TestTheLiveRejection:
    def test_the_old_tiering_reproduces_the_journal_exactly(self):
        """0.36 ATR, boundary at the session high, refused against the 0.75 bar."""
        e = em.estimate(_ctx(), _cand(), OLD)
        assert e.remaining_atr == pytest.approx(0.365, abs=0.01)
        assert e.boundary == pytest.approx(SESSION_HIGH, abs=1.0)
        assert e.time_fraction > 0.9, "94% of the session was still left"
        v = em.no_chase(_ctx(), _cand(), e, OLD)
        assert not v.ok and v.reason_code == "insufficient_remaining_move"

    def test_the_new_tiering_clears_the_gate(self):
        e = em.estimate(_ctx(), _cand(), NEW)
        assert e.remaining_atr > NEW.min_remaining_atr
        assert em.no_chase(_ctx(), _cand(), e, NEW).ok

    def test_the_cap_still_applies_it_is_only_softer(self):
        """Not removed from the cap: an extreme overhead is still resistance, so the
        estimate must land well below the uncapped figure."""
        capped = em.estimate(_ctx(), _cand(), NEW)
        uncapped = em.estimate(_ctx(level_atr=99.0), _cand(), NEW).remaining_atr
        assert capped.remaining_atr < uncapped
        assert "capped by session_high" in capped.notes


class TestTheTiering:
    def test_prior_period_extremes_stay_strong(self):
        assert set(NEW.strong_level_names) == {"pdh", "pdl", "pwh", "pwl"}
        assert "session_high" not in NEW.strong_level_names
        assert "session_low" not in NEW.strong_level_names

    def test_the_old_tiering_is_still_reachable(self):
        assert "session_high" in OLD.strong_level_names
        assert "session_low" in OLD.strong_level_names

    @pytest.mark.parametrize("level", ["pdh", "pwh"])
    def test_a_prior_period_high_still_gives_zero_credit(self, level):
        """The change must not quietly soften the levels that DO have acceptance
        behind them - that would be a different and unmeasured claim."""
        e = em.estimate(_ctx(name=level), _cand(), NEW)
        d = (SESSION_HIGH - SPOT) / ATR
        assert e.remaining_atr == pytest.approx(d, abs=0.01)

    def test_it_is_symmetric_for_shorts(self):
        """Seven of the fourteen were session_low on the short side."""
        ctx, cand = _ctx("down"), _cand("down")
        assert not em.no_chase(ctx, cand, em.estimate(ctx, cand, OLD), OLD).ok
        assert em.no_chase(ctx, cand, em.estimate(ctx, cand, NEW), NEW).ok


class TestTheGateStillRefusesRealChases:
    def test_a_dead_late_session_is_still_refused(self):
        """The gate's actual job. Retiering must not neuter it."""
        ctx = _ctx(hh=15, mm=10, regime="COMPRESSION", trend="NEUTRAL",
                   level_atr=0.1, hi=SPOT + 0.1 * ATR, lo=SPOT - 11 * ATR)
        assert not em.no_chase(ctx, _cand(), em.estimate(ctx, _cand(), NEW), NEW).ok

    def test_a_move_already_mostly_done_is_still_refused(self):
        ctx = _ctx(hh=14, mm=30, level_atr=0.05, hi=SPOT + 0.05 * ATR, lo=SPOT - 12 * ATR)
        cand = Candidate(strategy="LEVEL_REJECTION", version="v0.1", underlying="SENSEX",
                         direction="up", entry_ref=SPOT, invalidation=SPOT - 3.0 * ATR,
                         target_ref=SPOT + 2 * ATR, confirmation="x", counter_trend=False)
        assert not em.no_chase(ctx, cand, em.estimate(ctx, cand, NEW), NEW).ok


def test_config_carries_the_switch(monkeypatch):
    from trading_bot.engine.config import EngineConfig
    from trading_bot.engine.pipeline import PipelineParams
    cfg = EngineConfig.from_env()
    assert cfg.session_extremes_are_strong is False
    assert PipelineParams.from_config(cfg).move.session_extremes_are_strong is False
    monkeypatch.setenv("TECH_SESSION_EXTREMES_ARE_STRONG", "true")
    cfg = EngineConfig.from_env()
    assert cfg.session_extremes_are_strong is True
    assert PipelineParams.from_config(cfg).move.strong_level_names == (
        "pdh", "pdl", "pwh", "pwl", "session_high", "session_low")
