"""The provisional-score scale, and making regime coverage visible.

Both pin findings from the 2026-10-09 investigation into why the engine had
produced 131 signals and zero fills:

1. The pipeline scores twice - provisionally to RANK, then again once a strike
   exists. Two of the nine components describe the chosen option, so the
   provisional score could only earn 90 of 100 while being compared against a
   0-100 `min_score`. The ranking gate was ~11% stricter than it read.
2. `regime_incompatible` was the dominant rejection in the engine's entire
   history (358 family-rejections across 76 of 81 routing failures) and nothing
   in the journal said why. COMPRESSION is admitted by one family of eleven.
"""
import datetime as dt

from trading_bot.engine.context import ContextSnapshot
from trading_bot.engine.scoring import (CAPS, POST_SELECTION_COMPONENTS, PRE_SELECTION_MAX,
                                        pre_selection_min)
from trading_bot.engine.strategies import routable_families
from trading_bot.engine.strategies.families import FAMILIES

NOW = dt.datetime(2026, 10, 9, 10, 0, tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30)))


def _ctx(regime: str) -> ContextSnapshot:
    """Only `regime` matters for routable_families; everything else is filler."""
    return ContextSnapshot(
        ts=NOW, underlying="NIFTY", trigger_tf="5m", spot=22700.0, session_phase="09:15-10:00",
        quality="OK", bar_index=20, trends={}, alignment={}, regime={"primary": regime},
        structure={}, levels={}, price_action={}, indicators={"atr": 30.0}, volume={},
    )


class TestPreSelectionScale:
    def test_the_two_post_selection_components_are_named_and_total_ten(self):
        assert set(POST_SELECTION_COMPONENTS) == {"liquidity_execution", "option_quality"}
        assert sum(CAPS.values()) == 100
        assert PRE_SELECTION_MAX == 90
        assert sum(CAPS[k] for k in POST_SELECTION_COMPONENTS) == 10

    def test_min_score_is_scaled_to_the_achievable_maximum(self):
        assert pre_selection_min(20) == 18   # the force-fills setting
        assert pre_selection_min(30) == 27   # the previous setting
        assert pre_selection_min(40) == 36   # the shipped default
        assert pre_selection_min(90) == 81

    def test_zero_stays_zero_but_a_positive_threshold_never_rounds_away(self):
        """A threshold that rounded to 0 would admit every candidate, which is a
        far worse failure than being slightly strict."""
        assert pre_selection_min(0) == 0
        assert pre_selection_min(-5) == 0
        assert pre_selection_min(1) >= 1

    def test_the_scaled_gate_is_never_stricter_than_the_full_one(self):
        for m in range(0, 101):
            assert pre_selection_min(m) <= m

    def test_the_ranking_gate_actually_uses_it(self):
        """Pins the wiring, not just the helper - the bug was that the helper's
        job was being done by nobody."""
        import inspect

        from trading_bot.engine import pipeline
        src = inspect.getsource(pipeline.decide)
        rank_calls = [l for l in src.splitlines() if "rank(live" in l]
        assert len(rank_calls) == 1 and "ranking_min" in rank_calls[0], rank_calls
        assert "pre_selection_min(p.min_score)" in src
        # and the FULL threshold must still be re-applied after option selection
        assert "score_below_minimum_after_option_facts" in src


class TestRegimeCoverage:
    def test_compression_admits_exactly_one_family(self):
        """The finding that explains the engine's dominant rejection: a setup
        firing in compression collects ten regime_incompatible verdicts."""
        routable, total = routable_families(_ctx("COMPRESSION"))
        assert total == len(FAMILIES) == 11
        assert routable == 1
        names = [f.spec.name for f in FAMILIES if "COMPRESSION" in f.spec.compatible_regimes]
        assert names == ["COMPRESSION_BREAKOUT"]

    def test_low_volatility_admits_four(self):
        assert routable_families(_ctx("LOW_VOLATILITY"))[0] == 4

    def test_a_trending_regime_admits_most_families(self):
        assert routable_families(_ctx("STRONG_BULL"))[0] >= 9
        assert routable_families(_ctx("BULL"))[0] >= 9

    def test_never_trade_regimes_admit_nothing(self):
        for regime in ("NO_TRADE", "UNSTABLE"):
            assert routable_families(_ctx(regime))[0] == 0

    def test_an_unknown_regime_admits_nothing_rather_than_everything(self):
        assert routable_families(_ctx("SOMETHING_NEW"))[0] == 0

    def test_it_is_journaled_on_every_rejected_signal(self):
        import inspect

        from trading_bot.engine import paper_loop
        src = inspect.getsource(paper_loop)
        assert '"routable_families": routable_families(ctx)[0]' in src


class TestExplanationNoLongerLies:
    def test_the_m2_label_is_gone_from_the_codebase(self):
        """It claimed the strategy engines were unbuilt long after they shipped,
        and it appeared on every single rejection explanation."""
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent / "trading_bot"
        offenders = [p for p in root.rglob("*.py") if "strategy engines are M2" in p.read_text(encoding="utf-8")]
        assert offenders == []

    def test_the_strategy_line_reports_regime_coverage(self):
        import inspect

        from trading_bot.engine import pipeline
        src = inspect.getsource(pipeline.Decision.explanation)
        assert "routable_families(ctx)" in src
        assert "families accept regime" in src
