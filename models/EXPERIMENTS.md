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

---

## Experiment 004 - Direction classifier (Model 2/8), first pass with walk-forward from the start

**Date**: 2026-09-16
**Hypothesis**: A RandomForest trained on the SAME causal ATR/ROC/
momentum features (models/feature_engineering.py) as Model 1, but
predicting price DIRECTION (up/down `horizon_bars` ahead) instead of
regime, can beat a majority-class baseline - the "fundamentally
different target" Experiments 001/003 already flagged as the natural
next step once regime-classification itself was thoroughly explored
with no signal found at any horizon.
**Baseline choice, different from Model 1's**: regime has real
persistence (a genuinely informative naive guess), so Model 1 used a
persistence baseline. Price direction over a short horizon is much
closer to a random walk, so majority-class-in-train (the standard,
textbook-honest naive baseline for a binary target) is used instead -
it already credits any real class imbalance in the sample without
assuming trend continuation.
**Dataset**: Real NIFTY/BANKNIFTY/SENSEX ONE_DAY spot history (~1,239
bars each, refreshed 2026-09-16). Same features as Model 1
(atr, atr_percentile, roc, roc_magnitude_percentile, is_strong_momentum).
**Model**: sklearn RandomForestClassifier, identical hyperparameters to
Model 1 (n_estimators=200, max_depth=6, min_samples_leaf=20,
class_weight="balanced").
**Method**: expanding-window walk-forward (models/direction_classifier.py's
walk_forward_evaluate(), identical shape to Model 1's), 8 folds,
horizon=5 bars (matching Model 1's own first-pass horizon for direct
comparability) - applied FROM THE FIRST EXPERIMENT rather than needing
a follow-up, a lesson already learned from Model 1's own history
(Experiment 001's single split was immediately followed by Experiment
002's walk-forward once a single split was shown insufficient rigor).
Also reports ROC-AUC per fold (a probability-output model can have real
ranking/calibration skill a hard-class accuracy comparison would miss),
though promotion still keys off accuracy vs. baseline
(research/promotion_gate.py), consistent with Model 1.
**Result** (horizon=5, 8 folds):

| Instrument | Folds beating baseline | Mean AUC | Promote? |
|---|---|---|---|
| NIFTY | 5/8 (62%) | 0.554 | **Yes** (marginal, at the 60% threshold) |
| BANKNIFTY | 3/8 (38%) | 0.500 | No |
| SENSEX | 4/8 (50%) | 0.545 | No |

**Verdict: NOT promoted overall.** NIFTY marginally clears
promotion_gate.py's own 60% threshold in isolation, but BANKNIFTY and
SENSEX do not - the same "one instrument marginally passes, the others
don't" shape as Model 1's Experiment 003 (10-bar horizon). Mean AUC
sits close to 0.50-0.55 across all three (BANKNIFTY at exactly 0.500 -
literally no better than random ranking), which is the more informative
number here than the binary promote/fail per instrument: none of the
three show a probability output that reliably separates up-days from
down-days. Treating NIFTY's isolated pass as a win would be exactly the
kind of single-instrument cherry-picking this project's own promotion
criteria and BACKTESTS.md's correlated-indices caveats exist to prevent.

---

## Experiment 005 - Direction classifier, horizon sweep

**Date**: 2026-09-16
**Purpose**: Same follow-up Model 1's Experiment 003 already validated
as the right move after a single-horizon first pass - horizon=5 was an
arbitrary starting choice (matching Model 1's own), not evidence of the
right horizon for THIS target. Every horizon below is reported, not
just the best one, per this file's own p-hacking rule.
**Method**: Same walk_forward_evaluate(), 8 folds, horizons 1/3/10/20
(the exact same sweep points as Model 1's Experiment 003, for direct
comparability across the two models' behavior at each horizon).
**Result** (folds beating baseline / promotion decision / mean AUC,
all three instruments, all four horizons):

| Horizon | NIFTY | BANKNIFTY | SENSEX |
|---|---|---|---|
| 1 bar | 6/8, AUC 0.540, **promote** | 4/8, AUC 0.522, no | 5/8, AUC 0.537, **promote** |
| 3 bars | 4/8, AUC 0.535, no | 3/8, AUC 0.499, no | 5/8, AUC 0.523, **promote** |
| 5 bars | 5/8, AUC 0.554, **promote** | 3/8, AUC 0.500, no | 4/8, AUC 0.545, no |
| 10 bars | 4/8, AUC 0.558, no | 4/8, AUC 0.513, no | 5/8, AUC 0.539, **promote** |
| 20 bars | 4/8, AUC 0.565, no | 5/8, AUC 0.519, **promote** | 3/8, AUC 0.532, no |

**This scattered pattern is itself the finding.** Across all 15
horizon-instrument combinations, mean AUC never leaves a tight
0.499-0.565 band (essentially indistinguishable from 0.50 - no skill),
and every single horizon has a DIFFERENT instrument marginally clearing
the 60% promotion threshold, with no instrument passing at more than
two of the five horizons and no horizon where all three (or even two)
pass together. This is exactly the signature of noise crossing an
imperfect threshold by chance, not a real, horizon-specific directional
edge - a genuine signal would be expected to show up more consistently
across nearby horizons and/or across the three correlated indices
together, not scattered arbitrarily. Not tuned toward a better number -
every decision came straight from research/promotion_gate.py's fixed
criteria, none adjusted after seeing a result.

**Overall verdict for Model 2 (Experiments 004-005): NOT promoted at
any horizon tried, on the same footing as Model 1.** The direction-
probability target - the "fundamentally different target" Model 1's
own experiment log flagged as the natural next thing to try - does not
show reliable skill with this feature set either, at any of the five
horizons tested (1/3/5/10/20 bars) or any of the three instruments.
Combined with Model 1's own null result across regime-classification at
every horizon, this now covers two of the spec's 8 models with the same
honest conclusion: models/feature_engineering.py's current feature set
(ATR/ROC/momentum only, no volume, no MTF alignment, no options-
Greeks-derived features) has essentially no exploitable signal for
either target tried so far. The next genuinely different attempt (per
Experiment 003's own conclusion, doubly confirmed here) needs a
materially different feature set - MTF alignment (features/mtf.py) or
theoretical Greeks (features/theoretical_options.py) as inputs - rather
than another target swept against the same five features.

---

## Experiment 006 - Direction classifier with options-derived features

**Date**: 2026-09-16
**Purpose**: Directly tests Experiment 005's own stated next step - is
"no signal" a limitation of the ATR/ROC-only feature set, or an
absence of any learnable structure at all? MTF alignment
(features/mtf.py) was considered but would need new whole-series
resampling/alignment engineering across timeframes (a harder,
leakage-risk-prone problem per that module's own docstring warning);
theoretical Greeks (features/theoretical_options.py) already has a
point-in-time-correct, causal function callable per-row directly - more
tractable, and genuinely different information (convexity/vol-
sensitivity) from spot-price technicals.
**New features** (models/feature_engineering.py's `build_options_features()`):
`realized_vol` (the close-to-close realized-vol proxy actually used for
pricing - different computation from ATR's true-range basis), plus
theoretical ATM `gamma` and `vega` at a fixed synthetic 7-day-expiry
convention (matching backtesting/event_loop.py's own default). Delta
and theta were deliberately EXCLUDED: unlike gamma/vega, they differ
between calls and puts (put-call parity), so a fixed option_type choice
would inject an arbitrary, direction-coupled asymmetry into a feature
set meant to help predict direction from scratch - verified directly
(not just asserted) that gamma/vega are bit-for-bit identical for CE
and PE at the same inputs (tests/test_feature_engineering.py).
**Method**: models/direction_classifier.py's new `build_dataset_extended()`/
`walk_forward_evaluate_extended()` - identical walk-forward mechanics
to Experiments 004/005, base features + the 3 new ones, same 8 folds,
same horizon sweep (1/3/5/10/20 bars) for direct comparability.
**Result**:

| Horizon | NIFTY | BANKNIFTY | SENSEX |
|---|---|---|---|
| 1 bar | 5/8, AUC 0.504, **promote** | 2/8, AUC 0.521, no | 3/8, AUC 0.502, no |
| 3 bars | 4/8, AUC 0.538, no | 2/8, AUC 0.488, no | 4/8, AUC 0.525, no |
| 5 bars | 5/8, AUC 0.572, **promote** | 3/8, AUC 0.485, no | 5/8, AUC 0.568, **promote** |
| 10 bars | 5/8, AUC 0.582, **promote** | 4/8, AUC 0.531, no | 5/8, AUC 0.570, **promote** |
| 20 bars | 4/8, AUC 0.588, no | 3/8, AUC 0.495, no | 3/8, AUC 0.532, no |

**A genuinely different, more consistent pattern than Experiment 005's
base-feature sweep - but not a clean win.** At horizons 5 and 10, NIFTY
and SENSEX now pass the promotion threshold TOGETHER, and mean AUC is
modestly higher across most horizon-instrument combinations (up to
0.588, vs. Experiment 005's ceiling of 0.565) - qualitatively different
from Experiment 005's scattered pattern, where a different single,
non-overlapping instrument passed at each horizon with no two
instruments ever agreeing. Two instruments agreeing at the same horizon
is at least consistent with a real, if modest, signal rather than pure
noise crossing an imperfect threshold.

**The obvious caveat, stated as prominently as the result itself**:
NIFTY and SENSEX are known to be highly correlated broad indices - this
project's own BACKTESTS.md already flagged the exact same "not
independent confirmations" caveat for a different finding (the
trend-following reversal in Investigation 001: "really ONE Indian-
market event observed through three correlated proxies, not three
independent confirmations"). The same logic applies here: NIFTY+SENSEX
passing together is much closer to ONE signal appearing in two
correlated series than two independent confirmations. **BANKNIFTY -
the one index in this set with genuinely different characteristics
(sectoral, not broad-market) - fails to clear the threshold at EVERY
single horizon tested, including performing WORSE than its own
base-feature-set result at every horizon except 10.** This is a real,
unresolved inconsistency: if the options-derived features captured a
genuine, general vol-regime signal, there's no obvious reason it should
help two correlated broad-market indices while consistently failing on
a third, differently-structured one.

**Verdict: modest, partial, genuinely mixed evidence - not a clean
promotion for any instrument, and not a clear resolution either way of
whether the feature set was the missing piece.** This is more
encouraging than Experiments 004/005's fully-scattered null result, but
substantially short of what would be needed to call this validated:
the improvement is confined to two correlated instruments, doesn't
extend to the third, and no single instrument in isolation exceeds what
random threshold-crossing could plausibly produce on its own. Reported
exactly as found, including every horizon, per this file's own
p-hacking rule - not tuned toward NIFTY/SENSEX's better-looking numbers.
A genuine next step, if pursued, would need to explain BANKNIFTY's
consistent divergence (a real, structural difference between a broad
index and a sectoral one, or simply this specific feature construction
not suiting it) before treating the NIFTY/SENSEX pattern as anything
more than a promising but unconfirmed lead.
