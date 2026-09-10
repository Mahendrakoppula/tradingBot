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
