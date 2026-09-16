from research.framework.market_structure import StructureEvent
from research.framework.regime import Regime
from research.framework.scoring import ScoringInputs, ScoreWeights, score


def _regime(direction="up", strength=40.0, vol_bucket="normal"):
    return Regime(
        trend_direction=direction, trend_strength=strength, volatility_bucket=vol_bucket,
        plus_di=30.0, minus_di=10.0, atr=10.0, atr_percentile=50.0,
    )


def test_score_direction_must_be_up_or_down():
    try:
        score("sideways", _regime(), ScoringInputs())
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_score_everything_aligned_scores_near_max():
    regime = _regime(direction="up", strength=50.0, vol_bucket="normal")
    inputs = ScoringInputs(
        rsi=65.0, macd_histogram=1.5, relative_volume=2.5,
        recent_structure_events=[StructureEvent(kind="bos_up", index=10, price=100.0)],
        entry_trigger_confirmed=True,
    )
    sc = score("up", regime, inputs)
    assert sc.total > 90.0


def test_score_everything_opposed_scores_low():
    # Volume/volatility/entry-confirmation are direction-agnostic by design
    # (they describe overall market conditions, not directional evidence),
    # so isolate the directional sub-scores (trend/momentum/structure) by
    # leaving the direction-agnostic inputs at their neutral defaults.
    regime = _regime(direction="up", strength=50.0, vol_bucket="normal")
    inputs = ScoringInputs(
        rsi=65.0, macd_histogram=1.5,
        recent_structure_events=[StructureEvent(kind="bos_up", index=10, price=100.0)],
    )
    sc_down = score("down", regime, inputs)
    assert sc_down.total < 30.0


def test_score_no_data_is_neutral_not_zero():
    regime = Regime(trend_direction="none", trend_strength=None, volatility_bucket="unknown",
                     plus_di=None, minus_di=None, atr=None, atr_percentile=None)
    sc = score("up", regime, ScoringInputs())
    assert 0.0 < sc.total < 60.0  # neutral inputs shouldn't read as a strong signal either way


def test_score_respects_custom_weights():
    regime = _regime(direction="up", strength=50.0)
    inputs = ScoringInputs(entry_trigger_confirmed=True)
    heavy_entry = ScoreWeights(trend=0, momentum=0, structure=0, volume=0, volatility=0, entry_confirmation=100)
    sc = score("up", regime, inputs, heavy_entry)
    assert sc.total == 100.0
    assert sc.entry_confirmation == 100.0
