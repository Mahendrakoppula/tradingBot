import pytest

from research.experiment_log import Experiment, append_experiment, has_been_tried, load_experiments, promoted_experiments


def _experiment(**overrides) -> Experiment:
    defaults = dict(
        experiment_id="exp-001", date="2026-09-11", subject="models/regime_classifier",
        hypothesis="ATR/ROC features predict regime 5 bars ahead", method="walk-forward, 8 folds",
        metrics={"folds_beating_baseline": 1, "n_folds": 8}, verdict="REJECTED",
    )
    defaults.update(overrides)
    return Experiment(**defaults)


def test_invalid_verdict_raises():
    with pytest.raises(ValueError):
        _experiment(verdict="MAYBE")


def test_append_and_load_round_trip(tmp_path):
    log_path = tmp_path / "log.jsonl"
    exp = _experiment()
    append_experiment(exp, log_path=log_path)

    loaded = load_experiments(log_path=log_path)
    assert len(loaded) == 1
    assert loaded[0] == exp


def test_multiple_experiments_append_in_order(tmp_path):
    log_path = tmp_path / "log.jsonl"
    append_experiment(_experiment(experiment_id="exp-001"), log_path=log_path)
    append_experiment(_experiment(experiment_id="exp-002", verdict="PROMOTED"), log_path=log_path)

    loaded = load_experiments(log_path=log_path)
    assert [e.experiment_id for e in loaded] == ["exp-001", "exp-002"]


def test_load_from_nonexistent_log_returns_empty_list(tmp_path):
    assert load_experiments(log_path=tmp_path / "does_not_exist.jsonl") == []


def test_has_been_tried_matches_exact_subject_and_hypothesis(tmp_path):
    log_path = tmp_path / "log.jsonl"
    append_experiment(_experiment(subject="models/regime_classifier", hypothesis="H1"), log_path=log_path)

    assert has_been_tried("models/regime_classifier", "H1", log_path=log_path) is True
    assert has_been_tried("models/regime_classifier", "H2 - a different hypothesis", log_path=log_path) is False
    assert has_been_tried("strategies/portfolio", "H1", log_path=log_path) is False


def test_promoted_experiments_filters_by_subject_and_verdict(tmp_path):
    log_path = tmp_path / "log.jsonl"
    append_experiment(_experiment(experiment_id="a", subject="models/regime_classifier", verdict="REJECTED"), log_path=log_path)
    append_experiment(_experiment(experiment_id="b", subject="models/regime_classifier", verdict="PROMOTED"), log_path=log_path)
    append_experiment(_experiment(experiment_id="c", subject="strategies/portfolio", verdict="PROMOTED"), log_path=log_path)

    result = promoted_experiments("models/regime_classifier", log_path=log_path)
    assert [e.experiment_id for e in result] == ["b"]


def test_the_real_committed_log_is_backfilled_and_valid():
    """Regression check against research/experiment_log.jsonl itself
    (the default log_path, committed to git as real project history,
    not gitignored runtime state) - not a fixture. Confirms tonight's
    actual experiments (models/EXPERIMENTS.md, backtesting/BACKTESTS.md)
    were backfilled correctly and the file stays parseable."""
    experiments = load_experiments()
    assert len(experiments) >= 7
    assert has_been_tried(
        "models/regime_classifier",
        "RandomForest on causal ATR/ROC/momentum features predicts regime 5 bars ahead better than a persistence baseline",
    )
    rejected_regime_classifier = [e for e in experiments if e.subject == "models/regime_classifier" and e.verdict == "REJECTED"]
    assert len(rejected_regime_classifier) >= 2
