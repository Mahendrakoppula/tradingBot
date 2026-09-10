# Backtest Runs Log

Same discipline as models/EXPERIMENTS.md: every run gets an entry here,
and **a strategy/parameter is never tuned to look better against a
result already recorded here** - that's p-hacking against a fixed
in-sample run, not genuine improvement. Real validation needs
walk-forward/out-of-sample testing (Phase 13, not built yet), not
repeated tweaking against the same historical window.

---

## Bug found and fixed (2026-09-11): daily risk engine never reset

While running Monte Carlo validation (Run 003 below), BANKNIFTY showed
an implausible n_trades=5 over 5 years of daily bars - every other
result in this log has 40-100+ trades over the same span. Investigating
showed why: `backtesting/event_loop.py` created one `DailyRiskEngine`
per `run_backtest()` call and never reset it between bars. BANKNIFTY
happened to lose 5 trades in a row early in its history, breaching the
Rs.2,000 hard daily-loss limit - and since `_daily_pnl` never reset,
`allow_new_trades` stayed False for the remaining ~4.7 years of the
backtest. NIFTY and SENSEX had the same bug but it happened not to bite
as hard (no early enough losing streak to trip the permanent freeze),
so their numbers looked plausible enough that this went unnoticed until
an anomaly-check on a different instrument caught it.

**Fixed** by calling `daily_risk.reset_day()` once per bar - correct in
this daily-bars-only first pass specifically because each bar IS one
full trading day (see event_loop.py's own docstring on that scope
limitation; an intraday version of this engine would need to reset on
each new calendar day instead of every bar).

**Every number below dated 2026-09-11 or earlier reflects the ORIGINAL
BUGGY run.** They are left in place rather than deleted, with this
notice, because a validation log that quietly edits its own history
defeats the purpose of keeping one. Runs 001R and 002R below are the
corrected re-runs; treat the original Run 001/002 tables as void.

---

## Run 001 - First end-to-end plumbing check

**Date**: 2026-09-11
**Purpose**: Prove the event loop itself works correctly (leakage-free,
correct stop/target logic, correct P&L via the spot-proxy theoretical
option engine) - NOT a claim of a validated trading edge.
**Data**: Real NIFTY ONE_DAY spot history, 2021-09-13 to 2026-09-10
(1,241 bars), warmup_bars=30.
**Config**: Default BacktestConfig - ATR/structure stops
(risk/dynamic_stops.py defaults), the 3-strategy portfolio
(strategies/portfolio.py DEFAULT_STRATEGIES), daily risk engine defaults
(Rs.2,000 hard loss, Rs.800/1,000 selectivity), ATM strike, 7-day
fixed expiry, one conceptual unit (no lot-size, no margin, no
transaction costs, no slippage modeled at all - see event_loop.py's own
docstring for the full list of known simplifications).
**Result** (VOID - produced by the buggy engine, see the bug note
above; superseded by Run 001R):

| n_trades | win_rate | total_pnl (per unit) | max_drawdown |
|---|---|---|---|
| 42 | 28.6% | +2,425.42 | 1,013.87 |

**Verdict: not a validated result, informational only.** No
out-of-sample split, no transaction costs/slippage, no lot sizing, no
statistical significance testing (Phase 13). The positive total P&L
with a sub-30% win rate is at least internally consistent (the default
target is 2x the default stop distance in ATR terms, so a ~29% win rate
can still be net positive) - that consistency is itself a useful sanity
check that the wiring is correct, which was this run's actual goal.
Nothing here should be read as "the strategy works."

**What a real validation pass would need before any of this means
anything** (Phase 13 - see Run 002 below for a first pass at some of
this):
- A proper train/validation/test split with purged, embargoed
  walk-forward windows - this run used the FULL history with no holdout.
- Realistic transaction costs, slippage, and bid-ask spread on the
  option leg (currently zero).
- Real lot sizing and margin (currently one conceptual unit).
- Bootstrap/Monte Carlo resampling to get a sense of how much of this
  result could be pure luck given only 42 trades.
- Robustness checks across different regimes/instruments.

---

## Run 002 - Walk-forward across 5 folds, all three indices

**Date**: 2026-09-11
**Purpose**: Check whether Run 001's positive total P&L holds up across
several independent stretches of history, or was a feature of that one
full-history window (backtesting/walk_forward.py, spec's own "purged
CV, embargo periods" requirement).
**Data**: Real NIFTY/BANKNIFTY/SENSEX ONE_DAY spot history, 5
non-overlapping windows (~244 bars each) with a 5-bar embargo gap
between consecutive windows, each fold run as its own independent
backtest (own warmup, own daily-risk-engine state - see
walk_forward_backtest()'s own docstring).
**Config**: Same defaults as Run 001.
**Result** (VOID - produced by the buggy engine, see the bug note
above; superseded by Run 002R):

| Instrument | Folds profitable | Total trades | Mean P&L/fold | Std P&L/fold |
|---|---|---|---|---|
| NIFTY | 3/5 | 67 | +482.1 | 875.0 |
| BANKNIFTY | 3/5 | 41 | +736.7 | 3,172.4 |
| SENSEX | 3/5 | 40 | +1,367.2 | 2,801.0 |

**Verdict: inconclusive, not evidence of a validated edge.** 3 of 5
folds profitable on all three instruments is directionally
not-nothing, but the standard deviation of per-fold P&L exceeds or
rivals the mean in every single case (e.g. BANKNIFTY: one fold made
+6,284, another lost -2,470) - the result is dominated by a small
number of large-swing folds, not a consistent small edge showing up
fold after fold. With only 5 folds and 5-17 trades per fold, there is
nowhere near enough statistical power to distinguish this from noise.
Per this file's own rule, this was NOT tuned toward a better-looking
number - it's reported as found.

**Next step for a real answer**: bootstrap/Monte Carlo resampling
(spec's own next requirement) to estimate how much of this spread is
plausibly just sampling variance versus more folds (which needs either
more historical data than currently pulled, or a shorter bar interval
to get more independent trade opportunities per unit of wall-clock
history) before drawing any conclusion either way.

---

## Run 001R - Corrected full-history run (post-bugfix)

**Date**: 2026-09-11
**Data/config**: Same as Run 001, all three indices this time.
**Result**:

| Instrument | n_trades | win_rate | total_pnl | max_drawdown |
|---|---|---|---|---|
| NIFTY | 94 | 33.0% | +5,688.3 | 1,196.8 |
| BANKNIFTY | 97 | 28.9% | +14,859.4 | 6,414.4 |
| SENSEX | 92 | 34.8% | +24,596.2 | 3,687.5 |

Trade counts roughly doubled from the buggy run once the freeze was
lifted, on all three instruments - confirming the bug was suppressing
real trade opportunities across the board, not just on BANKNIFTY.

---

## Run 002R - Corrected walk-forward (post-bugfix)

**Date**: 2026-09-11
**Data/config**: Same as Run 002.
**Result**:

| Instrument | Folds profitable | Total trades | Mean P&L/fold | Std P&L/fold |
|---|---|---|---|---|
| NIFTY | 3/5 | 79 | +508.1 | 924.0 |
| BANKNIFTY | 3/5 | 82 | +1,091.6 | 3,965.0 |
| SENSEX | 3/5 | 75 | +2,786.7 | 3,902.4 |

**Still 3/5 folds profitable on all three, still high variance relative
to the mean.** The bugfix changed the absolute numbers a lot but not
the qualitative walk-forward conclusion: performance is not consistent
fold-to-fold. This matters a lot in light of Run 003 below - read them
together, not in isolation.

---

## Run 003 - Bootstrap and Monte Carlo (backtesting/monte_carlo.py)

**Date**: 2026-09-11
**Purpose**: Continues directly from Run 002R - is the observed
full-history total P&L (Run 001R) distinguishable from noise given the
actual trade-level distribution, and how much of the observed drawdown
was a lucky/unlucky ordering of the same outcomes?
**Method**: `bootstrap_total_pnl` (10,000+ resamples with replacement of
Run 001R's trade P&Ls) for a 90% CI on total P&L; `monte_carlo_trade_sequence`
(10,000+ reshufflings of the SAME trade outcomes, order only) for how
the observed max drawdown compares to other orderings of the identical
trades.
**Result**:

| Instrument | Bootstrap 90% CI | Fraction of resamples profitable | Observed max DD | 95th-pct DD across reorderings |
|---|---|---|---|---|
| NIFTY | [811.5, 10,749.4] | 97.3% | 1,196.8 | 2,500.1 |
| BANKNIFTY | [2,067.3, 28,046.4] | 97.3% | 6,414.4 | 6,435.4 |
| SENSEX | [8,358.8, 41,260.5] | 99.5% | 3,687.5 | 6,997.4 |

**Verdict: genuinely mixed, and the honest conclusion depends on which
question you're asking.** Treating the ~75-97 trades on each instrument
as an exchangeable bag (bootstrap), the total P&L is comfortably
distinguishable from zero on all three - every 90% CI is entirely
positive. But Run 002R's walk-forward, which preserves WHEN each trade
happened, still shows only 3 of 5 time-blocked folds profitable with
large swings between them. Both are correct, honest readings of the
SAME data: the aggregate distribution of trade outcomes looks favorable,
but that favorable aggregate is not showing up as a small, steady edge
repeating period after period - it is concentrated enough in particular
stretches of history that a walk-forward view (which is more sensitive
to timing) reads as inconsistent. **This should be read as suggestive,
not as proof of a validated edge**, for several concrete reasons:
- No out-of-sample test set has been held out at any point in this
  entire exploration - every number above is in-sample.
- Zero transaction costs, slippage, or bid-ask spread modeled.
- One conceptual unit, not real lot sizing/margin.
- NIFTY/BANKNIFTY/SENSEX all move together to a substantial degree
  (shared systematic market risk) - treating three positive results as
  three independent confirmations likely overstates the evidence.
- Bootstrap assumes trade outcomes are exchangeable/i.i.d., which
  understates risk if outcomes are actually autocorrelated (e.g.
  clustering in certain regimes) - plausible given the walk-forward
  fold-to-fold swings.

**Not tuned toward either a better or a more cautious-looking number** -
this is what both methods returned, reported together specifically so
neither the encouraging bootstrap result nor the discouraging
walk-forward inconsistency gets quietly dropped.
