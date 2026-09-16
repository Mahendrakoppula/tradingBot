"""Weighted multi-factor scoring engine: grades how well the CURRENT market
state supports taking a position in a GIVEN direction ("up" or "down") -
this module does not choose the direction itself, that's each strategy's
job (see strategies.py), since different strategy types want different
directions for different reasons (trend-following wants the regime's own
direction, mean-reversion wants the FADE of an RSI extreme, etc.). Keeping
"grade a direction" and "pick a direction" as separate concerns lets all
four strategy types reuse the same engine with their own weight emphasis.

Six sub-scores (trend, momentum, structure, volume, volatility, entry
confirmation) each contribute up to their configured weight; weights
default to summing to 100 so Score.total reads as a 0-100 percentage, but
nothing enforces that sum - a caller passing custom weights that sum to
something else just gets a differently-scaled total, which is fine since
every consumer (strategies.py) only compares Score.total against its own
min_score threshold in the same units.
"""
from dataclasses import dataclass, field

from research.framework.market_structure import StructureEvent
from research.framework.regime import Regime


@dataclass
class ScoreWeights:
    trend: float = 25.0
    momentum: float = 20.0
    structure: float = 20.0
    volume: float = 15.0
    volatility: float = 10.0
    entry_confirmation: float = 10.0


@dataclass
class ScoringInputs:
    rsi: float | None = None
    macd_histogram: float | None = None
    relative_volume: float | None = None  # see trading_bot.volume_analysis.relative_volume
    recent_structure_events: list[StructureEvent] = field(default_factory=list)  # ascending by index
    entry_trigger_confirmed: bool = False  # this bar's own breakout/crossover/volume trigger, if any


@dataclass
class Score:
    direction: str
    trend: float
    momentum: float
    structure: float
    volume: float
    volatility: float
    entry_confirmation: float
    total: float


def _trend_fraction(direction: str, regime: Regime) -> float:
    if regime.trend_direction == direction:
        return min(1.0, (regime.trend_strength or 0.0) / 50.0)
    if regime.trend_direction == "none":
        return 0.3  # neutral: a non-trending regime neither confirms nor contradicts
    return 0.0  # regime is actively trending the OPPOSITE way


def _momentum_fraction(direction: str, inputs: ScoringInputs) -> float:
    signals = []
    if inputs.rsi is not None:
        signals.append((inputs.rsi > 50) if direction == "up" else (inputs.rsi < 50))
    if inputs.macd_histogram is not None:
        signals.append((inputs.macd_histogram > 0) if direction == "up" else (inputs.macd_histogram < 0))
    if not signals:
        return 0.5  # no momentum data at all - neither reward nor penalize
    return sum(signals) / len(signals)


def _structure_fraction(direction: str, inputs: ScoringInputs) -> float:
    if not inputs.recent_structure_events:
        return 0.3  # neutral: no recent structural read either way
    last = inputs.recent_structure_events[-1]
    if not last.kind.endswith(f"_{direction}"):
        return 0.0  # most recent structural event points the OTHER way
    return 1.0 if last.kind.startswith("bos") else 0.8  # continuation vs. fresh reversal


def _volume_fraction(inputs: ScoringInputs) -> float:
    if inputs.relative_volume is None:
        return 0.5
    return min(1.0, max(0.0, inputs.relative_volume - 1.0))


_VOLATILITY_BUCKET_FRACTION = {"normal": 1.0, "high": 0.6, "low": 0.6, "unknown": 0.3}


def _volatility_fraction(regime: Regime, preferred_volatility: str | None) -> float:
    if preferred_volatility is None:
        return _VOLATILITY_BUCKET_FRACTION[regime.volatility_bucket]
    if regime.volatility_bucket == "unknown":
        return 0.0
    return 1.0 if regime.volatility_bucket == preferred_volatility else 0.4


def score(
    direction: str,
    regime: Regime,
    inputs: ScoringInputs,
    weights: ScoreWeights = None,
    *,
    preferred_volatility: str | None = None,
) -> Score:
    if direction not in ("up", "down"):
        raise ValueError(f"direction must be 'up' or 'down', got {direction!r}")
    weights = weights or ScoreWeights()

    trend_pts = weights.trend * _trend_fraction(direction, regime)
    momentum_pts = weights.momentum * _momentum_fraction(direction, inputs)
    structure_pts = weights.structure * _structure_fraction(direction, inputs)
    volume_pts = weights.volume * _volume_fraction(inputs)
    volatility_pts = weights.volatility * _volatility_fraction(regime, preferred_volatility)
    entry_pts = weights.entry_confirmation * (1.0 if inputs.entry_trigger_confirmed else 0.0)

    total = trend_pts + momentum_pts + structure_pts + volume_pts + volatility_pts + entry_pts
    return Score(
        direction=direction,
        trend=trend_pts,
        momentum=momentum_pts,
        structure=structure_pts,
        volume=volume_pts,
        volatility=volatility_pts,
        entry_confirmation=entry_pts,
        total=total,
    )
