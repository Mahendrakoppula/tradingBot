import datetime as dt
import logging
from collections import deque

from trading_bot.options import OptionChain, OptionContract

log = logging.getLogger(__name__)


class OpeningRangeTracker:
    """Tracks one underlying's spot high/low over a fixed reference window
    (default 09:15-11:15 IST, matching Zerodha's ORB-on-premium research -
    see research/README.md), then flags the FIRST close-based breakout of
    that range after the window ends. One instance per underlying per day;
    create a fresh one each morning.
    """

    def __init__(self, ref_start: dt.time, ref_end: dt.time):
        self.ref_start = ref_start
        self.ref_end = ref_end
        self.range_high: float | None = None
        self.range_low: float | None = None
        self.broken = False  # fire at most once per day

    def update(self, now_t: dt.time, ltp: float) -> str | None:
        if self.ref_start <= now_t < self.ref_end:
            self.range_high = ltp if self.range_high is None else max(self.range_high, ltp)
            self.range_low = ltp if self.range_low is None else min(self.range_low, ltp)
            return None
        if now_t < self.ref_start or self.range_high is None or self.broken:
            return None
        if ltp > self.range_high:
            self.broken = True
            return "CE"
        if ltp < self.range_low:
            self.broken = True
            return "PE"
        return None


class MomentumSpikeDetector:
    """Second, independent scalp signal: a fast directional move over a
    short rolling window (default 5 min) - can fire any time of day, unlike
    OpeningRangeTracker's fixed morning window. Combined with it per an
    explicit user choice ("combination of both, take whichever fires") -
    this is NOT a replacement for the ORB signal, it runs alongside it.
    """

    def __init__(self, window_minutes: int, min_move_pct: float):
        self.window = dt.timedelta(minutes=window_minutes)
        self.min_move_pct = min_move_pct
        self._samples: deque[tuple[dt.datetime, float]] = deque()

    def update(self, now: dt.datetime, ltp: float) -> str | None:
        self._samples.append((now, ltp))
        cutoff = now - self.window
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()
        if len(self._samples) < 2 or ltp <= 0:
            return None
        oldest_ltp = self._samples[0][1]
        if oldest_ltp <= 0:
            return None
        pct_change = (ltp - oldest_ltp) / oldest_ltp * 100
        if pct_change >= self.min_move_pct:
            return "CE"
        if pct_change <= -self.min_move_pct:
            return "PE"
        return None


def pick_scalp_signal(orb_signal: str | None, spike_signal: str | None) -> tuple[str | None, str]:
    """Combines the two independent scalp signals: either firing is enough
    to trigger a scalp entry (explicit user choice - "combination of both
    and the best", read as OR rather than requiring both simultaneously,
    which would rarely fire). ORB checked first since it's the
    higher-conviction, research-backed idea; momentum spike as the
    faster/more frequent secondary trigger.
    """
    if orb_signal is not None:
        return orb_signal, f"opening-range breakout -> {orb_signal}"
    if spike_signal is not None:
        return spike_signal, f"momentum spike -> {spike_signal}"
    return None, "no scalp signal"


def build_scalp_leg(chain: OptionChain, expiry: dt.date, spot: float, option_type: str) -> OptionContract:
    """Near-ATM strike selection, deliberately different from the daily
    strategy's fixed-%-OTM (build_long_leg in debit_strategy.py): the ORB
    research this is based on found closer-to-spot strikes (higher delta,
    lower theta drag) more responsive for a fast intraday move - see
    research/README.md, finding #1.
    """
    return chain.nearest_strike(expiry, option_type, spot)
