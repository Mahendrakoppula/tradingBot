"""Four independent strategy types, each a thin wrapper around
scoring.score() with its own weight emphasis and its own hard gates -
"independent" means each has a genuinely different candidate-direction
rule and different pre-score requirements, not just a different label on
the same logic:

- trend_following: rides the regime's own trend direction.
- breakout: requires an actual close-based breakout trigger (the caller
  supplies which direction broke out) confirmed by volume.
- mean_reversion: fades an RSI extreme, and refuses to fade a strong trend.
- momentum: requires RSI+MACD to already agree with a caller-supplied
  candidate direction before scoring it at all.

Every path - entered OR rejected - returns a machine-readable record
(Signal or NoTrade, both dict-serializable via dataclasses.asdict) with a
`reason` explaining the decision, per the "no trade is a valid decision"
principle: a NoTrade is not an absence of output, it's the output.
"""
from dataclasses import dataclass, field

from research.framework.regime import Regime
from research.framework.scoring import Score, ScoreWeights, ScoringInputs, score

TREND_FOLLOWING_WEIGHTS = ScoreWeights(trend=35, momentum=25, structure=15, volume=10, volatility=5, entry_confirmation=10)
BREAKOUT_WEIGHTS = ScoreWeights(trend=15, momentum=15, structure=20, volume=25, volatility=15, entry_confirmation=10)
MEAN_REVERSION_WEIGHTS = ScoreWeights(trend=5, momentum=30, structure=15, volume=15, volatility=25, entry_confirmation=10)
MOMENTUM_WEIGHTS = ScoreWeights(trend=15, momentum=35, structure=15, volume=20, volatility=5, entry_confirmation=10)


@dataclass
class Signal:
    strategy: str
    direction: str
    confidence: float  # the Score.total that produced this signal
    reason: dict = field(default_factory=dict)


@dataclass
class NoTrade:
    strategy: str
    reason: dict = field(default_factory=dict)


def _score_breakdown(sc: Score) -> dict:
    return {
        "total": sc.total,
        "trend": sc.trend,
        "momentum": sc.momentum,
        "structure": sc.structure,
        "volume": sc.volume,
        "volatility": sc.volatility,
        "entry_confirmation": sc.entry_confirmation,
    }


def trend_following(
    regime: Regime,
    inputs: ScoringInputs,
    *,
    weights: ScoreWeights = None,
    min_score: float = 60.0,
) -> Signal | NoTrade:
    weights = weights or TREND_FOLLOWING_WEIGHTS
    if regime.trend_direction == "none":
        return NoTrade("trend_following", {"rejected_because": "no_established_trend"})
    direction = regime.trend_direction
    sc = score(direction, regime, inputs, weights)
    if sc.total < min_score:
        return NoTrade(
            "trend_following",
            {"rejected_because": "score_below_threshold", "min_score": min_score, "score": _score_breakdown(sc)},
        )
    return Signal("trend_following", direction, sc.total, {"score": _score_breakdown(sc)})


def breakout(
    regime: Regime,
    inputs: ScoringInputs,
    candidate_direction: str,
    *,
    weights: ScoreWeights = None,
    min_score: float = 60.0,
    min_relative_volume: float = 1.5,
) -> Signal | NoTrade:
    weights = weights or BREAKOUT_WEIGHTS
    if not inputs.entry_trigger_confirmed:
        return NoTrade("breakout", {"rejected_because": "no_breakout_trigger_this_bar"})
    if inputs.relative_volume is None or inputs.relative_volume < min_relative_volume:
        return NoTrade(
            "breakout",
            {
                "rejected_because": "insufficient_volume_confirmation",
                "relative_volume": inputs.relative_volume,
                "min_relative_volume": min_relative_volume,
            },
        )
    sc = score(candidate_direction, regime, inputs, weights, preferred_volatility="high")
    if sc.total < min_score:
        return NoTrade(
            "breakout",
            {"rejected_because": "score_below_threshold", "min_score": min_score, "score": _score_breakdown(sc)},
        )
    return Signal("breakout", candidate_direction, sc.total, {"score": _score_breakdown(sc)})


def mean_reversion(
    regime: Regime,
    inputs: ScoringInputs,
    *,
    weights: ScoreWeights = None,
    min_score: float = 60.0,
    rsi_overbought: float = 70.0,
    rsi_oversold: float = 30.0,
    max_adx_to_fade: float = 25.0,
) -> Signal | NoTrade:
    weights = weights or MEAN_REVERSION_WEIGHTS
    if inputs.rsi is None:
        return NoTrade("mean_reversion", {"rejected_because": "no_rsi_reading"})
    if inputs.rsi >= rsi_overbought:
        direction = "down"
    elif inputs.rsi <= rsi_oversold:
        direction = "up"
    else:
        return NoTrade(
            "mean_reversion",
            {"rejected_because": "rsi_not_at_extreme", "rsi": inputs.rsi, "overbought": rsi_overbought, "oversold": rsi_oversold},
        )
    # Don't fade a strong trend running the opposite way to the fade direction.
    opposing_trend = regime.trend_direction != "none" and regime.trend_direction != direction
    if opposing_trend and (regime.trend_strength or 0.0) > max_adx_to_fade:
        return NoTrade(
            "mean_reversion",
            {
                "rejected_because": "trend_too_strong_to_fade",
                "trend_direction": regime.trend_direction,
                "trend_strength": regime.trend_strength,
                "max_adx_to_fade": max_adx_to_fade,
            },
        )
    sc = score(direction, regime, inputs, weights, preferred_volatility="low")
    if sc.total < min_score:
        return NoTrade(
            "mean_reversion",
            {"rejected_because": "score_below_threshold", "min_score": min_score, "score": _score_breakdown(sc)},
        )
    return Signal("mean_reversion", direction, sc.total, {"score": _score_breakdown(sc), "rsi": inputs.rsi})


def momentum(
    regime: Regime,
    inputs: ScoringInputs,
    candidate_direction: str,
    *,
    weights: ScoreWeights = None,
    min_score: float = 60.0,
) -> Signal | NoTrade:
    weights = weights or MOMENTUM_WEIGHTS
    if inputs.rsi is None or inputs.macd_histogram is None:
        return NoTrade("momentum", {"rejected_because": "insufficient_momentum_data"})
    rsi_agrees = (inputs.rsi > 50) if candidate_direction == "up" else (inputs.rsi < 50)
    macd_agrees = (inputs.macd_histogram > 0) if candidate_direction == "up" else (inputs.macd_histogram < 0)
    if not (rsi_agrees and macd_agrees):
        return NoTrade(
            "momentum",
            {
                "rejected_because": "momentum_not_confirmed",
                "candidate_direction": candidate_direction,
                "rsi": inputs.rsi,
                "macd_histogram": inputs.macd_histogram,
            },
        )
    sc = score(candidate_direction, regime, inputs, weights)
    if sc.total < min_score:
        return NoTrade(
            "momentum",
            {"rejected_because": "score_below_threshold", "min_score": min_score, "score": _score_breakdown(sc)},
        )
    return Signal("momentum", candidate_direction, sc.total, {"score": _score_breakdown(sc)})
