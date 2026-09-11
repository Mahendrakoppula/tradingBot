# Model Experiments Log

Every experiment gets an entry here - hypothesis, dataset, features,
model, params, result, honest verdict - per the spec's "multiple-testing
control" requirement (every experiment tracked, promotion only if
justified against a real baseline). This is the start of what a later
phase (the autonomous research engine) will eventually maintain
automatically; for now it's maintained by hand as models get built.

**Rule: never tune a model to beat a result already recorded here
against the SAME test set.** That's p-hacking against a fixed holdout,
not genuine improvement. A real improvement needs new data, a new
horizon/instrument, or a materially different feature/model design - not
a parameter sweep chasing this one number.

---

## Experiment 001 - Regime classifier (Model 1/8), first pass

**Date**: 2026-09-10/11
**Hypothesis**: A RandomForest trained on causal ATR/ROC/momentum
features (models/feature_engineering.py) can predict the market-state
regime 5 bars ahead better than assuming "the regime won't change"
(persistence baseline).
**Dataset**: Real NIFTY/BANKNIFTY/SENSEX ONE_DAY spot history (~1,241
bars each, 2021-09-13 to 2026-09-10), pulled via data/pull_history.py.
**Features**: atr, atr_percentile, roc, roc_magnitude_percentile,
is_strong_momentum (see models/feature_engineering.py's build_features).
**Model**: sklearn RandomForestClassifier, n_estimators=200, max_depth=6,
min_samples_leaf=20, class_weight="balanced".
**Split**: chronological, 80/20, 5-bar purge gap (models/regime_classifier.py).
**Result**:

| Instrument | n_train | n_test | Model accuracy | Persistence baseline |
|---|---|---|---|---|
| NIFTY | 974 | 244 | 0.512 | 0.566 |
| BANKNIFTY | 974 | 244 | 0.529 | 0.541 |
| SENSEX | 974 | 244 | 0.480 | 0.553 |

**Verdict: FAILED to beat baseline on all three instruments.** Not
promoted, not used anywhere. The training/evaluation pipeline itself
(causal features, time-ordered split with a purge gap, evaluation
against a real baseline with sample sizes reported) is validated and
working correctly - that was this pass's actual goal, per the plan's own
"prove the plumbing works before claiming a signal" scope. The specific
model/features tried here simply don't show skill at this horizon on
this data. Left as-is rather than tuned toward a better number on this
same test set (see the rule at the top of this file).

**Possible directions for a genuinely new experiment** (not started -
each of these would be Experiment 002+, run once, recorded honestly):
- A different horizon (1, 3, 10, 20 bars) - 5 bars was an arbitrary
  first choice, not validated as the right one.
- MTF alignment (features/mtf.py) as an added feature, once a phase
  wires multiple intervals into one training row.
- A shorter/more granular interval (ONE_HOUR, THIRTY_MINUTE) for more
  training rows per unit of wall-clock history.
- A fundamentally different target (e.g. Model 2's direction-probability
  from the spec's 8-model list) rather than iterating on this one.

---

## Experiment 002 - Regime classifier, multi-fold walk-forward validation

**Date**: 2026-09-11
**Purpose**: Experiment 001 only used a SINGLE 80/20 split - a single
train/test boundary can look bad (or good) by chance. Apply the same
walk-forward rigor already used for the strategy backtester (Phase 13)
to Model 1 itself: does "doesn't beat baseline" hold up across many
independent folds, or was that one split unlucky?
**Method**: models/regime_classifier.py's new walk_forward_evaluate() -
expanding-window walk-forward (fold i trains on ALL data up to a purged
cutoff, tests on the next held-out window, matching how a real
periodic-retrain system would behave), 8 folds, same features/model/
horizon as Experiment 001, minimum 10 rows per side per fold (smaller
folds silently skipped rather than reported - a "100% accuracy" from
one lucky guess is worse than no result).
**Result**: 24 total fold-instrument evaluations (8 folds x 3
instruments).

| Instrument | Folds beating baseline | Mean model accuracy | Mean baseline accuracy |
|---|---|---|---|
| NIFTY | 1/8 | 0.490 | 0.593 |
| BANKNIFTY | 1/8 | 0.453 | 0.569 |
| SENSEX | 0/8 | 0.490 | 0.594 |

**Verdict: CONFIRMED FAILED, much more conclusively than Experiment 001.**
The model beat the persistence baseline in only 2 of 24 total fold
evaluations (8.3%) - not a borderline or ambiguous result, a consistent
loss across nearly every independent time window on all three
instruments. This is strong evidence the current feature set (ATR/ROC/
momentum only) genuinely lacks predictive power for 5-bar-ahead regime
forecasting, not that Experiment 001's single split happened to be
unlucky. Per this file's own rule, NOT tuned toward a better number -
hyperparameters were left exactly as Experiment 001 set them.
**Conclusion for future work**: don't keep iterating on this exact
feature set at this horizon - a genuinely different experiment (new
features, a different horizon, or a different target entirely, per the
directions listed under Experiment 001) is needed before spending more
effort on Model 1's regime-classification task.

---

## Experiment 003 - Regime classifier, horizon sweep

**Date**: 2026-09-11
**Hypothesis**: 5 bars (Experiments 001/002) was an arbitrary first
choice for the prediction horizon - a different horizon might show real
skill even though 5 bars didn't. Same features/model, just varying
`horizon_bars`, per the "possible directions" list Experiment 001 itself
recorded.
**Method**: walk_forward_evaluate(), 8 folds, horizons 1/3/10/20 bars,
each result run through research/promotion_gate.py's objective
criteria (>=60% of folds beating baseline) rather than eyeballed.
**Result**: 12 horizon-instrument combinations, ALL rejected by the gate.

| Horizon | NIFTY | BANKNIFTY | SENSEX |
|---|---|---|---|
| 1 bar | 0/8 (0%) | 0/8 (0%) | 0/8 (0%) |
| 3 bars | 0/8 (0%) | 0/8 (0%) | 0/8 (0%) |
| 10 bars | 4/8 (50%) | 2/8 (25%) | 4/8 (50%) |
| 20 bars | 1/8 (12%) | 3/8 (38%) | 2/8 (25%) |

**Verdict: REJECTED at every horizon tried.** Short horizons (1, 3 bars)
fail outright because the persistence baseline itself becomes very
strong at short range (regimes rarely flip day-to-day) - 0/8 everywhere,
not close. The 10-bar horizon is the closest to parity (NIFTY/SENSEX
reach 50%, and their mean model accuracy slightly exceeds mean baseline
accuracy despite winning fewer than half the folds individually,
meaning winning folds have a larger margin than losing ones) - genuinely
the most interesting result in this sweep, but still well under the 60%
promotion threshold and BANKNIFTY doesn't follow the same pattern (25%).
20 bars degrades further as the feature set's short-horizon signals
(ATR/ROC over 10-14 bars) stop being relevant that far out. Not tuned
toward a better number - every horizon's promotion decision came
straight from research/promotion_gate.py's fixed criteria, none were
adjusted after seeing a result.
**Conclusion for future work**: the 10-bar result is the one direction
in this sweep worth a real follow-up (e.g. features tuned to that
horizon specifically, like a 10-bar-lookback ATR/ROC instead of the
current 14/10-bar defaults chosen for a 5-bar target) - everything else
tried so far (Experiments 001-003) points at the same conclusion: this
feature set has essentially no signal at short-to-medium horizons, and
a materially different feature set (MTF alignment, theoretical Greeks)
or a different target entirely (Model 2's direction-probability) is
needed before this regime-classification task is worth more direct
iteration.
