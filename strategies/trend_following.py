from strategies.base import Signal, Strategy


class TrendFollowingStrategy(Strategy):
    name = "trend_following"
    eligible_regimes = frozenset({"TRENDING_UP", "TRENDING_DOWN"})

    def generate(self, df, market_state, mtf=None):
        if not self.is_eligible(market_state):
            return None

        direction = "CE" if market_state.regime == "TRENDING_UP" else "PE"
        expected_mtf_direction = "UP" if direction == "CE" else "DOWN"

        # A clear MTF read (UP/DOWN/MIXED) that disagrees with this
        # direction means don't fight the higher timeframes - only
        # proceed if MTF has no opinion (None/UNKNOWN) or actually agrees.
        if mtf is not None and mtf.aligned_direction != "UNKNOWN" and mtf.aligned_direction != expected_mtf_direction:
            return None

        confidence = 0.5
        if market_state.momentum.label in ("STRONG_UP", "STRONG_DOWN"):
            confidence += 0.2
        if mtf is not None and mtf.aligned_direction == expected_mtf_direction:
            confidence += 0.3 * mtf.alignment_score
        confidence = min(confidence, 1.0)

        rationale = f"{market_state.regime} with momentum {market_state.momentum.label}"
        if mtf is not None:
            rationale += f", MTF alignment {mtf.alignment_score:.0%} toward {mtf.aligned_direction}"

        return Signal(self.name, direction, confidence, rationale)
