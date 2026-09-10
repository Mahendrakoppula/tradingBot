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
anything** (Phase 13, not started):
- A proper train/validation/test split with purged, embargoed
  walk-forward windows - this run used the FULL history with no holdout.
- Realistic transaction costs, slippage, and bid-ask spread on the
  option leg (currently zero).
- Real lot sizing and margin (currently one conceptual unit).
- Bootstrap/Monte Carlo resampling to get a sense of how much of this
  result could be pure luck given only 42 trades.
- Robustness checks across different regimes/instruments (only ran on
  NIFTY so far - BANKNIFTY/SENSEX not yet checked).
