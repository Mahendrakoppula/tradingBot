"""Multi-timeframe fusion (spec: 1m/5m/10m/30m/1H/Daily alignment/
conflict scoring). Deliberately does NOT resample intraday bars itself -
data/pull_history.py already fetches every interval directly from the
broker (see data/historical.py's MAX_DAYS_BY_INTERVAL), which sidesteps
a real gotcha: NSE/BSE sessions open at 09:15, which isn't a clean
boundary for naive pandas .resample("10min")/.resample("30min") anchored
at midnight - it would split the first bar of each day incorrectly. Each
timeframe here is classified independently via market_state.classifier
on its own broker-fetched series, then fused.

Leakage-safety note: this module classifies whatever OHLCV slice it's
given - it does NOT know or enforce whether a higher-timeframe bar has
actually "closed" relative to some live/backtest simulation clock. That
enforcement belongs to the event-driven backtester (a later phase) and
to live wiring, both of which must pass this module only bars that have
genuinely completed by the point in time being evaluated. A prior
project mistake (research/framework/mtf.py's aligned_view bug, comparing
a candidate bar's own start time instead of the next bar's start time as
proof of closure) is exactly the class of bug this note exists to avoid
repeating here.
"""
from dataclasses import dataclass, field

import pandas as pd

from market_state.classifier import MarketState, classify_market_state

# Low -> high timeframe order, matching data/historical.py's interval names.
TIMEFRAME_ORDER = ["ONE_MINUTE", "FIVE_MINUTE", "TEN_MINUTE", "THIRTY_MINUTE", "ONE_HOUR", "ONE_DAY"]


@dataclass
class MTFAlignment:
    per_timeframe_regime: dict[str, str] = field(default_factory=dict)
    aligned_direction: str = "UNKNOWN"  # "UP" | "DOWN" | "MIXED" | "UNKNOWN"
    alignment_score: float = 0.0  # fraction of directional timeframes agreeing with aligned_direction
    conflicting: bool = False  # True if at least one timeframe disagrees with another on direction


def _direction_of(regime: str) -> str | None:
    """RANGING/VOLATILE/UNKNOWN carry no directional vote - only a
    confirmed trend does. A timeframe that's currently ranging isn't
    "neutral evidence for both sides", it's simply not a vote."""
    if regime == "TRENDING_UP":
        return "UP"
    if regime == "TRENDING_DOWN":
        return "DOWN"
    return None


def fuse_states(states: dict[str, MarketState]) -> MTFAlignment:
    per_tf_regime = {tf: s.regime for tf, s in states.items()}
    directions = [d for tf in TIMEFRAME_ORDER if tf in states for d in [_direction_of(states[tf].regime)] if d is not None]

    if not directions:
        return MTFAlignment(per_tf_regime, "UNKNOWN", 0.0, conflicting=False)

    up, down, total = directions.count("UP"), directions.count("DOWN"), len(directions)
    if up > down:
        aligned_direction, agree = "UP", up
    elif down > up:
        aligned_direction, agree = "DOWN", down
    else:
        aligned_direction, agree = "MIXED", max(up, down)

    return MTFAlignment(
        per_timeframe_regime=per_tf_regime,
        aligned_direction=aligned_direction,
        alignment_score=agree / total,
        conflicting=up > 0 and down > 0,
    )


def classify_mtf(ohlcv_by_interval: dict[str, pd.DataFrame]) -> MTFAlignment:
    """Convenience wrapper: classify each interval's own market state,
    then fuse. `ohlcv_by_interval` keys should be interval names from
    TIMEFRAME_ORDER (extra/unknown keys are ignored for the directional
    vote but still appear in per_timeframe_regime)."""
    states = {interval: classify_market_state(df) for interval, df in ohlcv_by_interval.items()}
    return fuse_states(states)
