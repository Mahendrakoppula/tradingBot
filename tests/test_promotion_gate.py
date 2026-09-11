from research.promotion_gate import PromotionCriteria, evaluate_promotion


def test_too_few_folds_never_promotes_regardless_of_win_rate():
    decision = evaluate_promotion(n_folds=2, n_folds_beating_baseline=2)
    assert decision.promote is False
    assert "fold" in decision.reason.lower()


def test_below_threshold_fraction_does_not_promote():
    # 2/8 = 25%, below the default 60% threshold
    decision = evaluate_promotion(n_folds=8, n_folds_beating_baseline=2)
    assert decision.promote is False


def test_at_or_above_threshold_fraction_promotes():
    # 5/8 = 62.5%, above the default 60% threshold
    decision = evaluate_promotion(n_folds=8, n_folds_beating_baseline=5)
    assert decision.promote is True


def test_exact_boundary_fraction_promotes():
    criteria = PromotionCriteria(min_fraction_folds_beating_baseline=0.6, min_n_folds=5)
    decision = evaluate_promotion(n_folds=10, n_folds_beating_baseline=6, criteria=criteria)
    assert decision.promote is True


def test_custom_criteria_are_respected():
    strict = PromotionCriteria(min_fraction_folds_beating_baseline=0.9, min_n_folds=10)
    decision = evaluate_promotion(n_folds=10, n_folds_beating_baseline=8, criteria=strict)
    assert decision.promote is False  # 80% < 90% required


def test_model_1_real_result_is_correctly_rejected():
    """Sanity check against the actual Experiment 002 result
    (models/EXPERIMENTS.md) - 2 of 24 fold-instrument evaluations beat
    baseline. Per-instrument: 1/8, 1/8, 0/8 - all correctly rejected."""
    for n_beating in (1, 1, 0):
        decision = evaluate_promotion(n_folds=8, n_folds_beating_baseline=n_beating)
        assert decision.promote is False
