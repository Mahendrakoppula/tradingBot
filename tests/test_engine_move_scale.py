"""The expected-move scale, measured rather than guessed.

Two constants here were guessed, and both were low in the same direction.
Measured 2026-10-14 over 17 sessions of stored 5m candles:

                               was    measured median (BN / NIFTY / SENSEX)
    full-day range             6.0     9.45 / 12.13 / 12.24
    one-day directional travel 1.5     4.33 /  6.80 /  8.83

They compound: too small a `daily_range_atr_cap` inflates the "range already
used" share, which shrinks `range_scale`, which shrinks the estimate again.

Note on what this is NOT. The journaled no-chase rejections run 0.01-0.69 ATR,
which looks like the gate could never be passed - but that set is BY DEFINITION
everything below the threshold, so its maximum always sits just under it.
Signals above the bar passed and went deeper, and seven of them did. The defect
is the understated scale itself, which also feeds targets, the decay filter and
expected value - not an unreachable gate.
"""
import datetime as dt

import pytest

from trading_bot.engine import expected_move as em
from trading_bot.engine.context import ContextSnapshot
from trading_bot.engine.strategies.base import Candidate

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
ATR = 28.0          # NIFTY 5m ATR, as measured on 2026-10-08 (5.6 pts = 0.20 ATR)
SPOT = 22500.0
OLD = em.MoveParams(base_move_atr=1.5, daily_range_atr_cap=6.0)  # the shipped guesses


def _ctx(hh, mm, regime="BULL", used_atr=2.0, trend="BULL"):
    hi, lo = SPOT + used_atr * ATR / 2, SPOT - used_atr * ATR / 2
    return ContextSnapshot(
        ts=dt.datetime(2026, 10, 14, hh, mm, tzinfo=IST), underlying="NIFTY", trigger_tf="5m",
        spot=SPOT, session_phase="x", quality="OK", bar_index=20,
        trends={"5m": {"label": trend, "score": 0.6, "exhaustion": False}}, alignment={},
        regime={"primary": regime}, structure={},
        levels={"session_high": hi, "session_low": lo}, price_action={},
        indicators={"atr": ATR, "macd_hist": 0.4}, volume={})


def _cand(direction="up"):
    return Candidate(strategy="LEVEL_REJECTION", version="v1", underlying="NIFTY", direction=direction,
                     entry_ref=SPOT, invalidation=SPOT - 1.2 * ATR, target_ref=SPOT + 2 * ATR,
                     confirmation="close", counter_trend=False)


def test_the_measured_constants_are_what_shipped():
    p = em.MoveParams()
    assert p.base_move_atr == 4.0        # <= the lowest measured travel median (4.33), deliberately
    assert p.daily_range_atr_cap == 10.0  # just under the lowest measured range median (9.45)
    assert p.min_remaining_atr == 0.75    # the gate itself is unchanged


def test_the_old_scale_understated_a_full_session_badly():
    """The actual regression - and NOT "the gate was unreachable". It was reachable
    in ideal conditions (a strong early setup estimated 1.76 ATR under the old
    constants and passed). The defect is that a WHOLE SESSION with everything
    neutral was expected to travel barely one ATR, when the measured median
    one-way travel is 4.33-8.83. That understatement propagates into every target,
    decay-filter and expected-value calculation, not just this gate.
    """
    whole_session = em.estimate(_ctx(9, 15, "NORMAL", used_atr=0.0), _cand(), OLD)
    assert whole_session.remaining_atr < 1.5          # old: a full day is ~1 ATR
    corrected = em.estimate(_ctx(9, 15, "NORMAL", used_atr=0.0), _cand(), em.MoveParams())
    assert corrected.remaining_atr > 2.5              # new: in the measured band
    assert corrected.remaining_atr < 8.83             # never above the largest median


def test_the_corrected_scale_clears_the_gate_in_a_textbook_setup():
    e = em.estimate(_ctx(9, 35, "BULL", used_atr=1.0), _cand(), em.MoveParams())
    assert e.remaining_atr >= 0.75
    # and it stays within a plausible band: a full NIFTY day travels ~6.8 ATR one way,
    # so a morning estimate of several ATR is right and twenty would not be.
    assert 1.0 < e.remaining_atr < 8.0


@pytest.mark.parametrize("hh,mm,regime,used,expect_pass", [
    (9, 35, "BULL", 1.0, True),
    (10, 5, "BULL", 2.0, True),
    (11, 40, "NORMAL", 4.0, True),
    (14, 25, "BEAR", 8.0, True),
    (15, 10, "COMPRESSION", 9.0, False),   # late, compressed, range spent - still refused
])
def test_the_gate_still_discriminates_by_time_and_regime(hh, mm, regime, used, expect_pass):
    """Correcting the scale must not neuter the gate: its job is refusing entries
    with no room left, and a dead session late in the day still has none."""
    e = em.estimate(_ctx(hh, mm, regime, used_atr=used), _cand(), em.MoveParams())
    assert (e.remaining_atr >= 0.75) is expect_pass


def test_estimates_shrink_through_the_session():
    p = em.MoveParams()
    morning = em.estimate(_ctx(9, 30, "BULL", 1.0), _cand(), p).remaining_atr
    midday = em.estimate(_ctx(12, 30, "BULL", 1.0), _cand(), p).remaining_atr
    late = em.estimate(_ctx(15, 10, "BULL", 1.0), _cand(), p).remaining_atr
    assert morning > midday > late


def test_a_day_that_has_spent_its_range_has_less_left():
    p = em.MoveParams()
    fresh = em.estimate(_ctx(11, 0, "BULL", 1.0), _cand(), p).remaining_atr
    spent = em.estimate(_ctx(11, 0, "BULL", 12.0), _cand(), p).remaining_atr
    assert fresh > spent


def test_the_correction_raises_every_estimate_not_just_some():
    p = em.MoveParams()
    for hh, mm, reg, used in [(9, 30, "BULL", 1.0), (11, 0, "NORMAL", 4.0),
                              (13, 0, "RANGE", 6.0), (15, 0, "COMPRESSION", 9.0)]:
        old = em.estimate(_ctx(hh, mm, reg, used), _cand(), OLD).remaining_atr
        new = em.estimate(_ctx(hh, mm, reg, used), _cand(), p).remaining_atr
        assert new > old


def test_config_carries_the_scale(monkeypatch):
    from trading_bot.engine.config import EngineConfig
    from trading_bot.engine.pipeline import PipelineParams
    cfg = EngineConfig.from_env()
    assert cfg.base_move_atr == 4.0 and cfg.daily_range_atr_cap == 10.0
    monkeypatch.setenv("TECH_BASE_MOVE_ATR", "5.5")
    monkeypatch.setenv("TECH_DAILY_RANGE_ATR_CAP", "11.0")
    cfg = EngineConfig.from_env()
    p = PipelineParams.from_config(cfg)
    assert p.move.base_move_atr == 5.5 and p.move.daily_range_atr_cap == 11.0
