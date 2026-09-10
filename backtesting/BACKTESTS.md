# Backtest Runs Log

Same discipline as models/EXPERIMENTS.md: every run gets an entry here,
and **a strategy/parameter is never tuned to look better against a
result already recorded here** - that's p-hacking against a fixed
in-sample run, not genuine improvement. Real validation needs
walk-forward/out-of-sample testing (Phase 13, not built yet), not
repeated tweaking against the same historical window.

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
**Result**:

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
**Result**:

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
