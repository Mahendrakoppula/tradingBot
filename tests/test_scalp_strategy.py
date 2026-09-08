import datetime as dt

from trading_bot.options import OptionContract
from trading_bot.scalp_strategy import (
    MomentumSpikeDetector,
    OpeningRangeTracker,
    build_scalp_leg,
    pick_scalp_signal,
)


class FakeChain:
    def __init__(self, contract):
        self._contract = contract

    def nearest_strike(self, expiry, option_type, target_strike):
        assert option_type == self._contract.option_type
        return self._contract


def test_orb_tracker_builds_range_then_ignores_updates_after_break():
    tracker = OpeningRangeTracker(dt.time(9, 15), dt.time(11, 15))
    # Reference window: builds high/low, never signals.
    assert tracker.update(dt.time(9, 20), 100.0) is None
    assert tracker.update(dt.time(10, 0), 105.0) is None
    assert tracker.update(dt.time(10, 30), 98.0) is None
    assert tracker.range_high == 105.0
    assert tracker.range_low == 98.0

    # Before the window even starts: no signal, range untouched.
    fresh = OpeningRangeTracker(dt.time(9, 15), dt.time(11, 15))
    assert fresh.update(dt.time(9, 0), 100.0) is None
    assert fresh.range_high is None


def test_orb_tracker_fires_ce_on_upside_breakout():
    tracker = OpeningRangeTracker(dt.time(9, 15), dt.time(11, 15))
    tracker.update(dt.time(9, 20), 100.0)
    tracker.update(dt.time(10, 0), 105.0)
    tracker.update(dt.time(10, 30), 98.0)

    assert tracker.update(dt.time(11, 30), 106.0) == "CE"
    # Fires once per day - a second breakout past the same point is ignored.
    assert tracker.update(dt.time(11, 45), 110.0) is None


def test_orb_tracker_fires_pe_on_downside_breakout():
    tracker = OpeningRangeTracker(dt.time(9, 15), dt.time(11, 15))
    tracker.update(dt.time(9, 20), 100.0)
    tracker.update(dt.time(10, 0), 105.0)
    tracker.update(dt.time(10, 30), 98.0)

    assert tracker.update(dt.time(11, 30), 97.0) == "PE"


def test_orb_tracker_no_signal_while_inside_range():
    tracker = OpeningRangeTracker(dt.time(9, 15), dt.time(11, 15))
    tracker.update(dt.time(9, 20), 100.0)
    tracker.update(dt.time(10, 0), 105.0)
    tracker.update(dt.time(10, 30), 98.0)

    assert tracker.update(dt.time(11, 30), 102.0) is None


def test_momentum_spike_detector_fires_on_fast_move_within_window():
    detector = MomentumSpikeDetector(window_minutes=5, min_move_pct=0.1)
    base = dt.datetime(2026, 9, 8, 10, 0, 0)
    assert detector.update(base, 100.0) is None
    assert detector.update(base + dt.timedelta(minutes=2), 100.05) is None  # inside band
    assert detector.update(base + dt.timedelta(minutes=3), 100.20) == "CE"  # +0.20% within window


def test_momentum_spike_detector_drops_stale_samples_outside_window():
    detector = MomentumSpikeDetector(window_minutes=5, min_move_pct=0.1)
    base = dt.datetime(2026, 9, 8, 10, 0, 0)
    detector.update(base, 100.0)
    # 10 minutes later the old sample should have aged out of the 5-min
    # window, so a small move from the now-oldest sample shouldn't fire.
    assert detector.update(base + dt.timedelta(minutes=10), 100.05) is None


def test_pick_scalp_signal_prefers_orb_over_spike():
    signal, reason = pick_scalp_signal(orb_signal="CE", spike_signal="PE")
    assert signal == "CE"
    assert "opening-range breakout" in reason


def test_pick_scalp_signal_falls_back_to_spike():
    signal, reason = pick_scalp_signal(orb_signal=None, spike_signal="PE")
    assert signal == "PE"
    assert "momentum spike" in reason


def test_pick_scalp_signal_none_when_neither_fires():
    signal, reason = pick_scalp_signal(orb_signal=None, spike_signal=None)
    assert signal is None


def test_build_scalp_leg_targets_spot_itself_not_otm():
    contract = OptionContract(
        token="1", tradingsymbol="NIFTY08SEP2625000CE", name="NIFTY", expiry=dt.date.today(),
        strike=25000, option_type="CE", lotsize=25, freeze_qty=1800, exchange="NFO",
    )
    chain = FakeChain(contract)
    result = build_scalp_leg(chain, dt.date.today(), spot=25000.0, option_type="CE")
    assert result is contract
