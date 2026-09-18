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

---

## Investigation 001 - Why do specific walk-forward folds lose?

**Date**: 2026-09-11
**Purpose**: Turn Run 002R's "3/5 folds profitable, high variance"
observation into a testable, mechanism-level explanation rather than
leaving it as an unexplained number - using backtesting/attribution.py
(new: breaks trades down by strategy_name and by entry_regime, which
Trade now records at entry).
**Finding**: The LAST fold (bars 996-1240, 2025-09-16 to 2026-09-09) is
a losing fold on ALL THREE indices, and in every case the loss is
concentrated almost entirely in `trend_following` trades entered during
a TRENDING_UP regime classification - 0% win rate on those specific
trades, all three indices (NIFTY: 5 trades, -932.3; BANKNIFTY: 6
trades, -2,892.5; SENSEX: 6 trades, -3,141.1).
**Mechanism, confirmed against the actual price data**: NIFTY rallied
from ~24,611 to a peak of ~26,203 (Sep-Nov 2025), then reversed sharply
to ~22,331 by March 2026 (-15% from the peak) before chopping sideways
for the rest of the fold. Every trend_following CE entry taken during
that Sep-Nov rally (correctly classified as TRENDING_UP at the time,
per market_state's own swing-confirmation logic) rode straight into the
subsequent reversal. This is a well-known, well-documented
characteristic of trend-following strategies generally, not a bug:
they profit during sustained trends and lose at trend
reversals/market tops, by construction - exactly matching the pattern
in the WINNING folds too, where trend_following's biggest gains
(BANKNIFTY fold [498:742]: +4,981.4, 55.6% win rate) come from folds
containing sustained trends, not choppy reversals.
**Important caveat on independence**: NIFTY/BANKNIFTY/SENSEX move
together to a substantial degree. This "confirmed across three indices"
finding is really ONE Indian-market topping event (Nov 2025-Mar 2026)
observed through three correlated proxies, not three independent
confirmations - it should be read as one clear example of a
well-established general phenomenon (trend-following whipsaws at
reversals), not as fresh statistical proof from three separate data
points.
**Deliberately NOT acted on**: no parameter was tuned and no rule was
added to filter this specific reversal out - doing so now would mean
fitting a rule to the one topping event visible in this dataset, which
is p-hacking against a single historical instance dressed up as a
mechanism. A genuine mitigation (e.g. a trend-exhaustion/momentum-
divergence filter, or leaning on Model 2's eventual direction-
probability once it exists) is real future work, but it would need
validation on a DIFFERENT reversal event than this one to mean anything
- this dataset only contains one clear example of the phenomenon.

---

## Run 004 - Contract selector A/B test (strategies/contract_selection.py)

**Date**: 2026-09-11
**Purpose**: Phase 9's contract selector evaluates 5 strikes (ATM +/-2
increments) by capital efficiency (|delta|/premium - an honest
directional-leverage heuristic, NOT real risk-adjusted EV, since that
needs a probability model that doesn't exist - see the module's own
docstring) instead of always trading ATM. Tested as an explicit,
separately-logged A/B comparison against the existing ATM-only
baseline, config flag defaults to False specifically so this never
silently changes an already-logged result.
**Result** (full-history, same config as Run 001R otherwise):

| Instrument | n_trades | total_pnl (ATM-only, Run 001R) | total_pnl (selector) | max_dd (ATM-only) | max_dd (selector) |
|---|---|---|---|---|---|
| NIFTY | 94 | 5,688.3 | 6,512.4 | 1,196.8 | 802.5 |
| BANKNIFTY | 97 | 14,859.4 | 15,907.5 | 6,414.4 | 5,648.5 |
| SENSEX | 92 | 24,596.2 | 24,939.0 | 3,687.5 | 3,469.4 |

Same trade count on all three (strike choice doesn't affect WHEN/WHETHER
a trade fires, only which contract). A modest improvement in both total
P&L (+1% to +14%) and max drawdown on all three - re-ran the walk-forward
(002R) with the selector too: still exactly 3/5 folds profitable on all
three indices, same high variance relative to the mean. Confirms this
strike-selection change doesn't touch the DIRECTION/TIMING mechanism
Investigation 001 found (the trend-following-whipsaw-at-a-reversal
problem) - it wasn't expected to, since that's an entry-timing issue,
not a strike-selection one.

**Verdict: a plausible, mechanistically-sensible small improvement, not
a validated one.** Built before seeing this result (not tuned toward
it), and the effect size is modest relative to the bootstrap CI's own
width from Run 003 - not something to declare victory over. Config
still defaults to the ATM-only baseline; switching the default would
need its own out-of-sample validation, not a single in-sample A/B
comparison on the same history everything else in this log was checked
against.

---

## Run 005 - Realistic transaction costs (execution/transaction_costs.py)

**Date**: 2026-09-15
**Purpose**: Every prior run in this log has been logged with the same
caveat: "zero transaction costs, slippage, and bid-ask spread on the
option leg (currently zero)." Closes that specific gap directly - does
Run 001R/002R's positive P&L survive realistic Indian F&O round-trip
costs (brokerage, STT, exchange transaction charges, SEBI turnover fee,
stamp duty, GST), rather than being a pure artifact of a zero-cost
assumption?
**Method**: backtesting/cost_adjustment.py scales Run 001R's per-unit
premiums up to a REAL lot size (data/lot_size.py, verified live
2026-09-15 against the actual scrip master: NIFTY=65, BANKNIFTY=30,
SENSEX=20) and applies `main`'s own real, structurally-validated cost
formula (execution/transaction_costs.py, adapted from
trading_bot/costs.py) at its default, publicly-known-structure rates.
Re-ran BOTH the full-history result (001R) and the 5-fold walk-forward
(002R) with costs applied.
**Result** (full-history):

| Instrument | Gross total P&L | Net total P&L (after costs) | Total cost | Cost as % of gross | Wins flipped to losses |
|---|---|---|---|---|---|
| NIFTY | 369,739 | 363,140 | 6,599 | 1.8% | 0 / 31 |
| BANKNIFTY | 445,782 | 438,609 | 7,173 | 1.6% | 0 / 28 |
| SENSEX | 491,923 | 485,259 | 6,664 | 1.4% | 0 / 32 |

**Result** (walk-forward, 5 folds x 3 instruments = 15 evaluations):
profitable folds gross = 3/5 on all three indices; profitable folds net
(after costs) = 3/5 on all three indices - **zero sign flips at the
fold level either**, across all 15 fold-instrument combinations.

**Verdict: costs are a real but genuinely minor drag in this specific
backtest's parameter regime - they do NOT explain away the observed
P&L, and they do NOT change Run 002R's fold-level conclusion.** This is
NOT the same finding as `main`'s own costs.py motivation (small Rs.20-50/
lot scalp trades that real costs could plausibly wipe out) - this
backtester's ATR-based dynamic stop/target sizing (2:1 default reward:
risk on real index volatility) produces meaningfully larger per-trade
P&L swings than a tight scalp, so turnover-proportional costs end up a
small fraction of it by construction, not because costs are inherently
small for this instrument class. A tighter-stop/shorter-hold strategy
variant would likely show a much larger cost impact - not tested here.
**Important limitation carried over from data/lot_size.py's own
caveat**: this uses TODAY's live lot size/rates uniformly across 5 years
of historical trades - an illustrative "what would this look like at
today's real-world costs" analysis, not a historically-precise
reconstruction (lot sizes and cost rates have both changed multiple
times within this backtest's own window). Does not change the overall
Run 002R/003 verdict (inconclusive, suggestive-not-proof) - this run
only rules out "the observed P&L is just an artifact of ignoring
costs," it does not newly validate anything.

---

## Run 006 - Slippage/spread sensitivity sweep (backtesting/slippage_sensitivity.py)

**Date**: 2026-09-15
**Purpose**: Run 005 closed transaction costs with real, verifiable
statutory rates. Slippage/bid-ask spread is the other half of that same
gap - but unlike costs, there is NO real historical spread data to
derive a validated number from (SmartAPI never exposes historical
order-book depth; `main`'s own liquidity.py needs a LIVE quote).
Fabricating a single "the real slippage is X%" number would violate
this project's own discipline. Instead: a SENSITIVITY SWEEP across a
range of assumed round-trip slippage percentages (0%, 0.5%, 1%, 2%,
5% - split as a half-spread on each leg), combined with Run 005's real
transaction costs, answering "how much would the slippage assumption
have to be wrong before the conclusion changes" rather than pretending
to know the true number.
**Method**: backtesting/slippage_sensitivity.py's slippage_sensitivity_sweep(),
applied to both the full-history result (001R) and the 5-fold
walk-forward (002R), same real lot sizes as Run 005.
**Result** (full-history total P&L across the sweep):

| Instrument | 0% | 0.5% | 1% | 2% | 5% |
|---|---|---|---|---|---|
| NIFTY | 363,140 | 357,804 | 352,469 | 341,797 | 309,784 |
| BANKNIFTY | 438,609 | 432,211 | 425,812 | 413,015 | 374,624 |
| SENSEX | 485,259 | 479,654 | 474,049 | 462,838 | 429,207 |

Total P&L stays positive across the ENTIRE 0-5% sweep on all three
indices - even at an aggressive 5% assumed round-trip slippage, none
flip negative.

**Result** (walk-forward, 5 folds x 3 instruments x 5 slippage levels =
75 evaluations): the "3/5 profitable folds" finding from Run 002R holds
EXACTLY - zero fold flips sign at any slippage level tested, on any of
the three indices. The already-losing folds get modestly more negative
and the already-winning folds modestly less positive, but nothing
crosses zero across the whole tested range.

**Verdict: the P&L conclusion (both full-history and fold-level) is
robust to this entire plausible slippage range - it is NOT a fragile
result that a small execution-cost assumption would overturn.** This is
real, useful evidence, but it answers a narrower question than "is the
edge real": it only rules out "a plausible slippage assumption would
flip the conclusion." It does NOT change Run 002R/003's own verdict
(inconclusive - the bootstrap CI and the walk-forward fold-level
inconsistency are still in tension, for reasons unrelated to execution
costs). A materially worse slippage assumption than 5% (e.g. very
illiquid strikes, or a market-impact scenario beyond a simple spread
proxy) was not tested and could tell a different story - this sweep's
upper bound (5%) was a judgment call, not a validated ceiling on real
options slippage.

---

## Run 007 - Real account-equity simulation: is Rs.50,000 even enough capital?

**Date**: 2026-09-15
**Purpose**: Every prior run used a fixed illustrative lot size applied
uniformly to every trade (Run 005/006). This asks a different, more
fundamental question using backtesting/equity_simulation.py: starting
from the spec's OWN stated Rs.50,000 capital, with REAL risk-based
position sizing (a bought option's max loss is the premium paid - sized
against that, not a spot-points stop distance) and equity-protection
tiers actually engaged, what does a real account balance do across this
backtest's real trade sequence?
**Method**: simulate_equity_curve() processes Run 001R's real trades in
chronological order, sizing each one from CURRENT capital x
base_risk_pct x the current equity-protection tier's multiplier, against
that trade's REAL entry premium x real lot size (data/lot_size.py) as
the affordability check, with real transaction costs applied
(execution/transaction_costs.py).

**Result - core finding, before any tuning**: at Rs.50,000 starting
capital and a CONSERVATIVE 1-5% risk-per-trade (the range any
disciplined risk framework would recommend, and this project's own
DEFAULT_BASE_RISK_PCT), **100% of trades were skipped as unaffordable,
on all three indices** - not 1%, not occasionally, all of them:

| Instrument | n trades | Median premium x lot_size (= 1-lot max loss) | Risk % needed for the CHEAPEST trade in the dataset |
|---|---|---|---|
| NIFTY (lot=65) | 94 | Rs.9,000 (18.0% of Rs.50k) | 8.4% |
| BANKNIFTY (lot=30) | 97 | Rs.10,358 (20.7% of Rs.50k) | 10.6% |
| SENSEX (lot=20) | 92 | Rs.9,246 (18.5% of Rs.50k) | 7.7% |

This is a REAL, structural fact about 2026-era Indian index-option
economics (real lot sizes from SEBI's 2024-2025 contract-value
revisions, real theoretical premiums from real spot levels/volatility),
not a bug: buying even one whole lot of a near-ATM NIFTY/BANKNIFTY/
SENSEX option, at this backtester's chosen strikes/expiry, costs a
double-digit percentage of Rs.50,000 - incompatible with single-digit
per-trade risk discipline for a whole-lot-buying approach.

**Result - what happens at higher (non-conservative) risk levels**: swept
10%/15%/20%/30% base_risk_pct (with max_lots=1, to bound the worst-case
blowup - see caveat below):

| Instrument | 10% | 15% | 20% |
|---|---|---|---|
| NIFTY | 1 trade, -8% | 1 trade, -13% | 92 trades, **+767%**, 15.9% max DD |
| BANKNIFTY | 0 trades, +0% | 1 trade, -6% | 1 trade, -16% |
| SENSEX | 1 trade, -9% | 81 trades, **+865%**, 14.0% max DD | 1 trade, -20% |

**Verdict: this is evidence of severe undercapitalization, NOT a
trading signal - the wild swings themselves are the finding.** Whether
the outcome is "one trade then stuck skipping everything else" (a small
loss) or a huge headline return depends ENTIRELY on whether the very
first affordable trade happens to win or lose - classic gambler's-ruin-
adjacent dynamics when bet size is forced large relative to bankroll.
The "+767%"/"+865%" numbers are NOT validated returns and must never be
quoted as if they were: they are what happens when a lucky early win
lets fixed-fractional sizing compound on a growing capital base for the
rest of a 5-year backtest - a well-known artifact of naive percentage-
of-capital position sizing, not evidence of skill. (Uncapped, i.e.
without max_lots=1, the same dynamic is far worse: NIFTY reached +1925%
with an 84.7% max drawdown, SENSEX +9665% with 68.7% - included only to
show why max_lots matters, never as a headline number.)

**Actionable conclusion, not a code problem to fix quietly**: either (a)
the spec's Rs.50,000 capital figure needs revisiting for a whole-lot-
buying approach on these three specific indices at current real lot
sizes, or (b) the system needs strike selection that specifically
targets premiums cheap enough to fit a genuinely conservative risk
budget (strategies/contract_selection.py's current capital-efficiency
criterion does not consider absolute affordability at all - a concrete,
well-scoped future improvement this finding directly motivates), or (c)
defined-risk multi-leg structures (spreads) instead of naked long
options, which the spec's own contract-selector section calls for and
this project hasn't built yet. Not resolved here - reported as found.

---

## Run 008 - Affordability-aware strike selection (follow-up to Run 007)

**Date**: 2026-09-15
**Purpose**: Direct follow-up to Run 007's own recommended next step.
strategies/contract_selection.py's new select_affordable_contract()
walks strikes outward from ATM, strictly in the OTM direction, and
returns the first (least-far-OTM) contract whose real premium fits a
given risk budget - reusing each trade's ORIGINAL entry timing/
direction/expiry (Investigation 001 and Run 004 established strike
choice is orthogonal to entry timing FOR NEAR-ATM STRIKES - see the
important caveat on that below), only re-pricing at a different strike.
**Method**: backtesting/equity_simulation.py's new
simulate_equity_curve_with_affordable_contracts(), same real lot sizes
and transaction costs as Run 007.

**Headline result (full 5-year history, one continuously-compounding
run)**: at a CONSERVATIVE 1% risk-per-trade, all three indices now size
and take nearly every trade (94/94 NIFTY, 94/97 BANKNIFTY, 91/92 SENSEX -
vs. 0/94, 0/97, 0/92 in Run 007). Ending capital: NIFTY +667%, BANKNIFTY
+584%, SENSEX +896% over the full period.

**These full-history percentages are NOT the right number to trust or
quote** - they compound continuously across the ENTIRE 5-year backtest
with no capital ever withdrawn, so a handful of large early gains get
multiplicatively amplified all the way through. The more honest view:
independent walk-forward folds, each starting FRESH at Rs.50,000 (no
cross-fold compounding) - same 5-fold windows as Run 002R/006:

| Instrument | Folds profitable | Fold returns (range) | Max drawdown (range) |
|---|---|---|---|
| NIFTY | 4/5 | -8.1% to +112.2% | 2.4% to 8.1% |
| BANKNIFTY | 2/5 | -12.0% to +119.6% | 2.6% to 13.0% |
| SENSEX | 3/5 | -8.7% to +132.4% | 1.9% to 8.7% |

This is a much more moderate, believable picture - directionally mixed
(4/5, 2/5, 3/5), similar "suggestive not proof" character as Run 002R's
own original ATM-based finding, not a uniform win.

**Traced one large winning trade in full detail (NIFTY, entry_index=83)
to understand the mechanism, not just trust the number**: a PUT bought
~3.8% out of the money (strike 17,400 vs spot 18,056), premium Rs.5.57,
delta -0.036. Spot then moved -5% and crossed straight through the
strike; by exit the option was solidly in the money and its premium
had risen to Rs.250.90 - confirmed as genuine, correctly-priced
intrinsic value (matches Black-Scholes-near-expiry math exactly), NOT a
bug. This is a real, legitimate options payoff - but it also means the
aggregate return is disproportionately driven by a small number of such
"far-OTM-to-ITM" hits, a well-known high-variance/fat-tail
characteristic of buying cheap OTM options, not a steady, broad-based
edge.

**Two important, unaddressed risks - explicitly flagged, not resolved**:
1. Run 006's slippage sensitivity sweep (0-5%) was built and calibrated
   against ATM-level premiums (Rs.60-1,200 range, per Run 005's table).
   The trades in THIS run are priced at far smaller premiums (Rs.5-15 at
   entry) where real bid-ask spreads, as a PERCENTAGE of premium, are
   typically much worse for illiquid far-OTM contracts in real markets -
   this has not been tested for this specific, much-lower-premium
   contract profile, and Run 006's assumed range may understate real
   execution cost here materially.
2. Investigation 001/Run 004's "strike selection doesn't affect entry
   timing" finding was established comparing NEAR-ATM strikes (+/-2
   increments, similar delta/convexity). Extending that same assumption
   all the way to FAR-OTM strikes (5-9+ increments out, delta ~0.03-0.13,
   fundamentally different convexity) has NOT been separately validated -
   reusing the same spot-price-based stop/target framework (calibrated
   implicitly around near-ATM sensitivity) for a very different payoff
   shape is an unverified assumption, not a proven equivalence.

**Verdict: directionally real progress on Run 007's literal problem
(Rs.50,000 CAN now participate, at a genuinely conservative risk
level), but the exciting-looking headline numbers must not be trusted
or quoted as validated returns.** The fold-level walk-forward view is
the right number to look at, and it shows a mixed, inconclusive picture -
consistent with everything else in this log, not a breakthrough. Real
next steps before this means anything further: a slippage/liquidity
model appropriate to illiquid, low-premium far-OTM contracts (not
reusing Run 006's ATM-calibrated range), and separately validating the
entry-timing-reuse assumption specifically for far-OTM convexity rather
than assuming Run 004's near-ATM finding extends that far. Neither done
here - reported as found, not oversold.

**Update (2026-09-18) - re-verifying this Run's original BANKNIFTY
numbers after finding a real strike-increment bug elsewhere**: while
investigating models/EXPERIMENTS.md's Experiment 006, a real bug was
found and confirmed against the live scrip master - BANKNIFTY's true
near-the-money strike spacing is Rs.100, not Rs.50. That confirmed bug
affected Experiment 006 and BACKTESTS.md's Run 013 (both computed
directly in this same later session) - this Run's OWN original
BANKNIFTY figures were computed in an earlier, separate session whose
exact code isn't available to inspect, so rather than assume either
way, they were independently re-verified using confirmed-correct
strike increments (NIFTY 50, BANKNIFTY 100, SENSEX 100) against
current data:

| Instrument | Headline (re-verified) | Headline (original) | Walk-forward (re-verified) | Walk-forward (original) |
|---|---|---|---|---|
| NIFTY | +663.6% | +667% | 5/5, +2.5% to +112.2% | 4/5, -8.1% to +112.2% |
| BANKNIFTY | +586.3% | +584% | 3/5, -11.9% to +213.3% | 2/5, -12.0% to +119.6% |
| SENSEX | +882.1% | +896% | 4/5, -9.5% to +145.5% | 3/5, -8.7% to +132.4% |

**Reassuring, not alarming**: headline numbers and each instrument's
worst fold match the original very closely across ALL THREE indices
(BANKNIFTY's -11.9% vs -12.0% and +586.3% vs +584% are nearly
identical) - strong evidence this Run's original BANKNIFTY figure
already used the correct Rs.100 spacing, unlike Experiment 006/Run 013.
The remaining differences (fold-profitable counts, some individual
fold values, particularly BANKNIFTY's best fold +213.3% vs +119.6%)
show up on NIFTY too, whose strike_increment never changed between the
original run and this re-verification - proving those differences are
natural DATA DRIFT (data/pull_history.py's rolling window has shifted
forward several days since this Run was first computed on 2026-09-15,
changing exactly which historical bars fall in each fold near its
edges), not a parameter bug. Stated with appropriate hedging, not
overclaimed: the exact original code isn't available to confirm with
certainty, but the evidence points clearly toward "already correct,"
closing the open question left by Run 013's own correction note.

---

## Run 009 - Tick-based slippage for far-OTM contracts (first of Run 008's two flagged risks)

**Date**: 2026-09-15
**Purpose**: Directly closes the first of Run 008's two explicitly
flagged, unaddressed risks: Run 006's slippage sweep (0-5% of premium)
was calibrated against ATM-level premiums (Rs.60-1,200) and likely
understates real cost for the much smaller far-OTM premiums (Rs.5-15)
Run 008's affordability-aware selection actually trades. A real bid-ask
spread on an illiquid contract is better modeled as a roughly fixed
number of exchange TICKS (an absolute rupee amount) than a fixed
fraction of premium - a few ticks is trivial for an expensive ATM
option and can be most of the premium for a cheap far-OTM one.

**A data-interpretation catch worth recording on its own**: the live
scrip master's own `tick_size` field reads "5.000000" for every
NIFTY/BANKNIFTY/SENSEX option. Taken literally that's an absurd Rs.5
minimum tick for options trading as low as Rs.0.01-0.05 (confirmed
earlier this session). This schema scales OTHER price-like fields by
100 - directly confirmed via the `strike` field, whose raw value
"2300000.000000" matches a real 23000 strike embedded in that same
row's own tradingsymbol text. Applying the same convention to
tick_size gives the real value: Rs.0.05, not Rs.5 - a 100x error that
would have made every number in this run meaningless if taken at face
value. execution/transaction_costs.py-style "verify against real data,
don't assume" discipline caught this before it became a bug.

**Method**: backtesting/tick_slippage.py (stateless sweep, Run 005/006
style, for a fixed lot size) and a new `tick_spread` parameter added
directly to equity_simulation.py's compounding affordable-contract
simulation (Run 007/008 style) - the compounding case has to re-run the
WHOLE trade sequence per tick level, since realized cost changes the
capital available to size every subsequent trade, unlike a stateless
post-hoc reprice.

**Result (full-history, NIFTY, 1% risk)** - swept far beyond a
"plausible" range specifically to find where (if anywhere) this
actually breaks the Run 008 result, not just to confirm it survives a
token check:

| Ticks (round-trip) | Rupees/unit | Trades simulated | Ending capital |
|---|---|---|---|
| 0 | 0.00 | 94 | +666.6% |
| 20 | 1.00 | 94 | +654.9% |
| 100 | 5.00 | 94 | +596.4% |
| 200 | 10.00 | 94 | +516.2% |
| 350 | 17.50 | 93 | +255.8% |
| **400** | **20.00** | **12** | **-17.2%** |
| 800 | 40.00 | 4 | -16.2% |

**There is a real, sharp THRESHOLD, not a gradual decay**: results stay
strongly positive all the way through Rs.17.50 round-trip (already
100%+ of the affordable contract's own premium), then between Rs.17.50
and Rs.20.00 the number of trades the account can even afford collapses
from 93 to 12 and the result flips to a loss. This is a real, useful
finding about the FRAGILITY of the compounding mechanism itself once
costs get large enough to choke off position sizing early, not just
"returns get a bit worse" - qualitatively different behavior above vs.
below the threshold. Whether a real Rs.20/unit round-trip spread
(133-400% of a Rs.5-15 premium) is a plausible worst case or an
unrealistically extreme one for actual NSE far-OTM index-option
liquidity is NOT something this project can verify (no real historical
spread data exists - see backtesting/slippage_sensitivity.py's own
docstring) - reported as a real threshold that exists, not as evidence
either way about whether it would be reached in practice.

**Result (walk-forward, more decision-relevant than the full-history
number per Run 008's own warning)** - profitable folds out of 5, same
windows as every other walk-forward result in this log:

| Instrument | 0 ticks | 20 ticks (Rs.1) | 50 ticks (Rs.2.50) | 100 ticks (Rs.5) |
|---|---|---|---|---|
| NIFTY | 4/5 | 4/5 | 4/5 | 3/5 |
| BANKNIFTY | 2/5 | 2/5 | 2/5 | 2/5 |
| SENSEX | 3/5 | 3/5 | 3/5 | 3/5 |

**Verdict: the fold-level, decision-relevant picture is essentially
UNCHANGED across a substantial, realistic-to-aggressive tick-slippage
range (up to Rs.5/unit, a third to half of the affordable contract's
own premium) - only NIFTY loses one fold at the highest level tested.**
This closes Run 008's first flagged risk with a genuine, not-assumed
answer: tick-based (premium-scale-aware) slippage does NOT change the
mixed, inconclusive walk-forward conclusion within a plausible range,
though a real, sharp breakdown threshold does exist far out at the
full-history level if costs get extreme enough. Run 008's SECOND
flagged risk (validating the entry-timing-reuse assumption specifically
for far-OTM convexity, not just Run 004's near-ATM finding) remains
open, not addressed here.

---

## Run 010 - Moneyness-path analysis for far-OTM exits (second of Run 008's two flagged risks)

**Date**: 2026-09-15
**Purpose**: Closes Run 008's second flagged risk. Investigation
001/Run 004 established that changing STRIKE SELECTION doesn't affect
ENTRY TIMING - but on reflection that's true by construction in this
codebase (`strategies/*.process_bar()` decides entry/exit purely from
spot price action; it never looks at premium or strike at all), so it
was never really the open question. The real question: does reusing
the SAME spot-price-based stop/target EXIT framework
(`risk/dynamic_stops.py`, built and implicitly calibrated around
near-ATM sensitivity, before strike selection existed at all) produce a
MEANINGFUL exit for a FAR-OTM contract, whose premium only moves
substantially once spot actually approaches or crosses that contract's
own, much more distant strike? If "target" exits are dominated by
trades where spot never got anywhere near the selected strike, that
would be real evidence the exit framework's "target hit" label doesn't
mean what it's implicitly assumed to mean for far-OTM contracts.

**Method**: new `backtesting/moneyness_analysis.py`. For every
affordability-priced simulated trade (Run 008's
`simulate_equity_curve_with_affordable_contracts()`), scans the FULL
hold window's bar-by-bar high/low (not just the entry/exit snapshot -
a mid-hold spike that reverses by exit would be invisible to an
endpoint-only check) for the closest spot ever came to the
affordability-selected strike, and records whether spot actually
crossed it. Results grouped by the ORIGINAL backtest's `exit_reason`
(stop/target/end_of_data, decided purely in spot terms) to see whether
"target hit" trades are the ones where the selected strike was
genuinely approached/crossed. `SimulatedTrade` extended with
`strike`/`entry_premium`/`exit_premium` fields (the actual
affordability-selected values, not the original ATM-based
`trade.strike`) to make this auditable; backward-compatible, confirmed
via the existing 18-test `test_equity_simulation*.py` suite.

**Result** (same trades as Run 008/009, 1% risk, full history):

| Instrument | Exit reason | n | Crossed strike | Mean closest moneyness |
|---|---|---|---|---|
| NIFTY | stop | 65 | 19 (29.2%) | +0.81% (still OTM) |
| NIFTY | target | 28 | 28 (100.0%) | -1.66% (ITM) |
| NIFTY | end_of_data | 1 | 1 (100.0%) | -0.09% |
| BANKNIFTY | stop | 70 | 14 (20.0%) | +1.30% (still OTM) |
| BANKNIFTY | target | 23 | 17 (73.9%) | -1.40% (ITM) |
| BANKNIFTY | end_of_data | 1 | 1 (100.0%) | -0.83% |
| SENSEX | stop | 62 | 23 (37.1%) | +0.34% (still OTM) |
| SENSEX | target | 29 | 29 (100.0%) | -1.98% (ITM) |

**A clear, consistent pattern across all three indices**: "target"
exits are overwhelmingly real - spot genuinely crossed the far-OTM
strike during the hold in 74-100% of target-exit trades, with the mean
closest approach solidly ITM (-1.4% to -2.0%), not a coincidental
label. This is the opposite of what the flagged risk worried about:
the far-OTM contract's own convexity mechanism (spot actually reaching
the strike) IS what's driving "target hit" trades, not an artifact of
reusing a near-ATM-calibrated framework. **"Stop" exits tell a
different story**: only 20-37% of stop-exit trades ever saw spot
approach the selected strike at all, and the mean closest approach
stayed clearly OTM (+0.3% to +1.3%) - most stop-outs are NOT convexity
events in the underlying at all. That's expected and not itself a bug
(a bought option's premium can erode past a stop threshold from pure
theta decay or a small vol move with spot going nowhere near the
strike - that's a real, distinct loss mechanism from "wrong-way move");
it does mean the spot-based stop threshold is, for most of these
trades, effectively a decay/small-move filter rather than a
directional-conviction check tied to the contract's own strike.

**Verdict: Run 008's second flagged risk is resolved, and resolved
favorably for the "target" side of the framework specifically** - the
far-OTM exit-timing-reuse concern does not materialize for target hits;
they are real, strike-crossing convexity events, consistently across
NIFTY/BANKNIFTY/SENSEX. The finding does surface a separate, smaller,
newly-noticed nuance (not one of the original two flagged risks, and
not investigated further here): most "stop" exits are premium-decay
events rather than strike-approach events, which is a legitimate but
previously undocumented distinction in what "stop" actually means for
a far-OTM bought option versus a near-ATM one. Not itself flagged as
broken - just noted as a real mechanism difference worth being aware of
if `risk/dynamic_stops.py`'s thresholds are ever tuned specifically for
far-OTM contracts in the future.

---

## Run 011 - First intraday (5-minute bar) backtest, and a real bug found building it

**Date**: 2026-09-15
**Purpose**: Closes a gap disclosed in event_loop.py's own module
docstring since Run 001: "Runs on ONE_DAY bars only... a true intraday
event loop is a natural extension of this same engine, not built here."
Every single result in this log through Run 010 has used ONE_DAY bars
exclusively, despite the live system's spec targeting intraday
decision-making, and despite 5-minute/hourly OHLCV already being pulled
locally for all three indices (data/raw/*/FIVE_MINUTE.parquet etc.) and
simply never used.

**A real bug found and fixed before any intraday result could be
trusted**: `process_bar()` called `daily_risk.reset_day()`
unconditionally on every call, correct only because "each call IS one
full trading day" held for every backtest run so far. Feeding 5-minute
bars into the unmodified function would have reset the daily-loss kill
switch every 5 minutes instead of once per real day - silently
defeating it (the same bug CLASS, opposite direction, as the
daily-risk-never-resetting bug this log already caught via Monte Carlo
validation). **Fix**: `process_bar()` now takes an explicit
`is_new_day` flag (default `True`, preserving the exact original
behavior for every existing caller/test unchanged), and `run_backtest()`
now computes it from each bar's REAL calendar date via a new
`_bar_date()` helper, resetting only on an actual day boundary. Four
new tests added (`tests/test_event_loop.py`) proving the flag gates the
reset correctly and that `run_backtest()`'s own loop derives it from
real dates, not bar position. Full existing suite (394 tests, all of
Runs 001-010) still passes unchanged - this is a pure extension, not a
behavior change for daily bars.

**Honest caveat carried into every number below, stated once here
rather than repeated per line**: `market_state/volatility.py`'s
ATR period (14) and volatility-percentile lookback (100) - along with
every strategy's own lookback parameters - were tuned/validated
exclusively against DAILY-bar semantics (ATR(14) = 14 trading days,
lookback(100) = ~5 months). Run unchanged on 5-minute bars, the SAME
numbers mean ATR(14) = ~70 minutes and lookback(100) = ~8 hours - a
completely different, much noisier real-world volatility measure. This
run is an honest FIRST LOOK at what the existing rule set does at
intraday cadence, not a separately-tuned or separately-validated
intraday strategy. The spot-proxy Black-Scholes options-pricing
limitation (features/theoretical_options.py, unchanged since Run 001)
also applies, and matters more here: real intraday bid-ask microstructure
and execution friction at near-scalping trade frequency isn't modeled
beyond the same round-trip cost formula used for the much-lower-frequency
daily backtests.

**Data**: ~4 months of real 5-minute OHLCV (2026-05-13 to 2026-09-10,
85 trading days, ~6,325 bars/index) for all three indices - a much
shorter window than the 5-year daily dataset every other run in this
log uses; the sample-size/robustness caveat below is a direct
consequence of this.

**Result - same window, 5-minute bars vs. daily bars, one-conceptual-unit
P&L, zero cost** (fair comparison: the daily side is DAILY bars
restricted to the exact same May-Sep 2026 window, not the usual 5-year
history):

| Instrument | Bars | n trades | Win rate | Total P&L (pts) | Exit reasons |
|---|---|---|---|---|---|
| NIFTY | 5-min | 466 | 33.9% | +3,241.0 | target 126, stop 340 |
| NIFTY | daily | 6 | 33.3% | -231.8 | stop 4, target 1, eod 1 |
| BANKNIFTY | 5-min | 468 | 32.7% | +9,528.8 | target 118, stop 350 |
| BANKNIFTY | daily | 4 | 50.0% | -726.3 | stop 3, eod 1 |
| SENSEX | 5-min | 467 | 36.2% | +12,194.8 | target 138, stop 329 |
| SENSEX | daily | 5 | 20.0% | -742.2 | stop 4, target 1 |

**Per-trade mechanism check (NIFTY 5-min, before trusting the total)**:
mean win Rs.41.10 (n=158) vs. mean loss Rs.-10.56 (n=308) - a real,
asymmetric payoff (small losses cut quickly by tight ATR-based stops,
larger occasional wins), not an artifact of one outlier: the single
largest trade is 9.6% of total P&L, and the top 5 trades together are
33.6% - concentrated but not degenerate. Trade durations: median 7 bars
(35 min), max 35 bars (~3 hours) - genuinely intraday holds, consistent
with the much-shorter effective ATR window. Entry/exit premiums sampled
by hand are sane, real option-premium magnitudes (Rs.25-58 near-ATM),
not a numerical artifact.

**Cost-adjusted (real lot sizes: NIFTY 65, BANKNIFTY 30, SENSEX 20,
live-verified 2026-09-15)** - the critical check, since trade count
exploded roughly 80x vs. the daily comparison and Run 005 found costs
were only a minor 1.4-1.8% drag at low trade counts:

| Instrument | Gross P&L (Rs.) | Net P&L (Rs.) | Total cost (Rs.) | Cost as % of gross |
|---|---|---|---|---|
| NIFTY | 210,667.6 | 187,277.8 | 23,389.8 | 11.1% |
| BANKNIFTY | 285,862.9 | 262,047.2 | 23,815.7 | 8.3% |
| SENSEX | 243,896.0 | 220,326.3 | 23,569.7 | 9.7% |

Costs are meaningfully higher as a share of gross than Run 005's daily
result (8-11% vs 1.4-1.8%), exactly as expected with far more trades -
but the result survives comfortably, still strongly net positive on
all three.

**Walk-forward (4 non-overlapping folds within the 85-day window,
~1 trading day embargo)**:

| Instrument | Fold 0 | Fold 1 | Fold 2 | Fold 3 | Folds profitable |
|---|---|---|---|---|---|
| NIFTY | +1,751.2 | +267.7 | +338.5 | +275.4 | 4/4 |
| BANKNIFTY | +3,029.9 | +2,805.8 | +2,473.8 | +13.0 | 4/4 |
| SENSEX | +5,445.7 | +1,821.3 | +1,892.7 | +1,400.8 | 4/4 |

**Bootstrap (10,000 resamples, full 85-day sample, one-conceptual-unit
P&L)**:

| Instrument | Observed | 90% CI | Fraction of resamples profitable |
|---|---|---|---|
| NIFTY | 3,241.0 | [1,972.4, 4,584.1] | 100.0% |
| BANKNIFTY | 9,528.8 | [5,818.0, 13,285.5] | 100.0% |
| SENSEX | 12,194.8 | [7,631.5, 17,014.1] | 100.0% |

**This is the cleanest, most consistent positive result in this entire
log** - 12/12 walk-forward folds profitable (no single fold or single
trade dominating), bootstrap confidence intervals comfortably excluding
zero on all three indices, and the result survives realistic
transaction costs at the real trade frequency. That is a genuinely
different character from every other run here, which has consistently
been mixed or marginal (Runs 001-010's daily-bar walk-forward results
were never better than 4/5 folds on any single index). **This must NOT
be read as "intraday is a validated edge" without the caveats already
stated above carrying real weight**:
1. Only ~4 months / 85 trading days of data - a much thinner evidentiary
   base than the 5-year, 5-fold daily walk-forward used throughout this
   log. Four ~3-week folds is a far weaker out-of-sample claim than five
   ~1-year folds.
2. Every parameter (ATR period, volatility lookback, strategy-specific
   lookbacks) is running at a real-world timescale roughly 100x shorter
   than what it was tuned/validated against on daily bars - this result
   describes what the EXISTING rule set happens to do at 5-minute
   cadence, not a strategy anyone deliberately designed or validated for
   that cadence.
3. Spot-proxy theoretical options pricing (no real historical intraday
   premium/bid-ask data) is a bigger leap at near-scalping trade
   frequency than at the daily cadence every other run in this log used
   it at.
4. This is one continuous window studied once, not a fresh out-of-sample
   period distinct from where the parameters themselves originated (the
   daily-bar strategies were tuned/tested on 2021-2026 daily data that
   overlaps this same May-Sep 2026 span) - a formal look-ahead risk this
   analysis does not fully rule out.

**Verdict**: real, working, correctness-verified intraday backtesting
capability now exists (the day-boundary bug fix is the durable
deliverable), and the FIRST look through it is unusually positive and
survives every honesty check applied so far (per-trade mechanism, real
transaction costs, walk-forward, bootstrap). Reported exactly as found,
not undersold - but the short window and daily-tuned parameters mean
this is a promising lead to investigate further (a longer intraday
history, and/or an intraday-specific parameter pass, both separately
logged and neither done here), not yet a validated edge on the same
footing as this log's 5-year daily-bar findings.

---

## Run 012 - Same rule set, real hourly data, ~6x more history (498 vs 85 days)

**Date**: 2026-09-15
**Purpose**: Run 011's two flagged caveats were (1) only ~4 months of
5-minute data, far thinner than this log's usual 5-year daily window,
and (2) ATR/lookback parameters tuned against daily-bar semantics
running at a real-world timescale roughly 100x shorter than validated
for. Two ways to address this were considered: pull more history, or
tune parameters specifically for intraday cadence. **Parameter tuning
was deliberately NOT done**: it would mean tuning against the exact
same window that already produced Run 011's striking positive result -
the textbook in-sample-overfitting trap this log's own standing
discipline exists to rule out ("never tuned to look better against an
already-logged result"), with no held-out data to tell a real
improvement apart from fitting noise. Longer history carries no such
risk, and turned out to be free: `data/raw/*/ONE_HOUR.parquet` already
holds 498 real trading days (2024-09-10 to 2026-09-10, all three
indices) - no new data pull needed. A bonus, not the main motivation:
ATR(14)/lookback(100) at hourly granularity represents ~2.3 trading
days / ~17 trading days - still shorter than the daily-tuned intent,
but meaningfully closer to it than Run 011's 5-minute run (~70 min /
~8 hours), so the parameter-mismatch caveat is somewhat less severe
here specifically.

**No code changes required** - Run 011's day-boundary fix
(`is_new_day` computed from real calendar dates via `_bar_date()`)
already generalizes to any intraday granularity; this run only swaps
which parquet file gets loaded.

**Result (real lot sizes, 5-fold walk-forward with a 6-bar/~1-day
embargo, 10,000-resample bootstrap)**:

| Instrument | n trades | Win rate | Mean win / mean loss | Cost as % of gross | Folds profitable | Bootstrap 90% CI | Frac. resamples profitable |
|---|---|---|---|---|---|---|---|
| NIFTY | 276 | 37.3% | Rs.169.18 / Rs.-45.23 | 2.5% | 5/5 | [6,423.5, 12,848.0] | 100.0% |
| BANKNIFTY | 273 | 36.6% | Rs.483.56 / Rs.-110.13 | 1.9% | 5/5 | [20,471.4, 38,597.5] | 100.0% |
| SENSEX | 265 | 38.5% | Rs.535.46 / Rs.-151.77 | 2.5% | 5/5 | [19,532.8, 40,535.7] | 100.0% |

**Per-trade mechanism check (NIFTY)**: even more distributed than Run
011's 5-minute result - the single largest trade is 4.8% of total P&L
(top 5 combined: 21.0%), median hold 6 bars (~6 trading hours), max 27
bars (~4.5 trading days). Sampled entry/exit premiums are sane, real
option-premium magnitudes. Costs are a noticeably SMALLER drag than
both Run 011's 5-minute result (8-11%) and even Run 005's original
daily result (1.4-1.8%) in relative terms per index here (1.9-2.5%) -
consistent with fewer, larger-premium-move trades relative to the
fixed per-trade cost, not a red flag.

**15/15 walk-forward folds profitable across all three indices, over
~1.4 years of real out-of-sample data** - directly and substantially
addresses Run 011's #1 caveat (thin sample). This is corroborating,
not identical, evidence: it validates the SAME rule set at a
DIFFERENT intraday granularity (hourly, not 5-minute) over a much
longer real window, not a re-run of the exact 5-minute finding with
more data. The two results are consistent with each other (both
strongly positive, both well-distributed, both survive real costs)
but should be read as two separate, mutually reinforcing data points,
not one result confirmed twice.

**Caveats that still apply, carried over honestly, not resolved by
this run**: spot-proxy theoretical options pricing (no real historical
intraday premium/bid-ask data) remains unchanged from every prior run
in this log. The hourly ATR/lookback windows are closer to daily-tuned
intent than the 5-minute run's were, but still meaningfully shorter -
this remains the existing rule set's first look at hourly cadence, not
a separately validated hourly-native strategy. The 498-day window still
overlaps the same historical period the daily-bar strategies were
originally built/tested against (2021-2026 daily data) - a formal
look-ahead risk this analysis does not fully rule out, same as noted
in Run 011.

**Verdict**: the most robust intraday evidence in this log so far.
Two different intraday granularities (5-minute over 4 months, hourly
over 1.4 years) both show the SAME rule set producing consistent,
well-distributed, cost-surviving, walk-forward-robust, bootstrap-
confirmed positive results - genuinely more compelling than a single
lucky window would look like. Still not claimed as a validated,
production-ready edge: no intraday-specific parameter validation has
been attempted (deliberately, to avoid p-hacking against these same
results), and the spot-proxy pricing/look-ahead-overlap caveats remain
real, disclosed, unresolved limitations. The next honest step, if
pursued, would be sourcing genuinely fresh out-of-sample intraday data
(a period not already touched by anything this project has tuned or
validated against) rather than tuning parameters against what's
already been seen.

---

## Investigation 002 - Sourcing genuinely fresh out-of-sample intraday data

**Date**: 2026-09-15
**Purpose**: Run 011/012 both flagged the same unresolved
look-ahead-overlap caveat: their intraday data (5-minute, May-Sep 2026;
hourly, Sep 2024-Sep 2026) overlaps the same 2021-2026 span the
daily-bar strategies were themselves originally built/tuned against -
not a fresh, independent test. The only way to remove that overlap
entirely is data collected strictly AFTER any tuning/validation ever
touched this project, i.e. going forward from today. Intraday
parameter tuning was already explicitly ruled out in Run 012 as a
p-hacking risk - this is the other, safe half of that same decision.

**No new infrastructure needed - confirmed by using what already
exists**: `data/pull_history.py` (the exact script already scheduled
daily on the shared EC2 instance via `deploy/codex-paper-trading.timer`,
10:15 UTC / 15:45 IST, Mon-Fri) already backfills FIVE_MINUTE and
ONE_HOUR among its intervals, and `data/storage.py`'s `save_ohlcv()`
already dedups-and-merges by timestamp - so simply re-running it
extends the local dataset forward safely, with no risk of corrupting
or duplicating existing bars. Rather than build a parallel
forward-collection mechanism, or touch the shared EC2 instance (which
was stopped at the time, per its normal 6am-8pm IST schedule, and this
local machine's own AWS deployer credentials don't have log/SSM read
access to inspect it anyway - confirmed via `aws ssm
list-command-invocations`, AccessDeniedException, by design per this
project's scoped-IAM discipline), this session ran `python -m
data.pull_history` directly on this local machine, which already has
working SmartAPI credentials. A safe, read-only, already-tested,
already-in-production operation - identical to what already runs daily
on the EC2 box.

**Result: the refresh succeeded cleanly, zero errors, across all three
indices and all six intervals** - confirms the mechanism genuinely
works end to end, not just in theory. New coverage:

| Instrument | Interval | Old range end | New range end | New bars vs. Run 011/012 snapshot |
|---|---|---|---|---|
| NIFTY | FIVE_MINUTE | 2026-09-10 | 2026-09-15 | 147 (2 new trading days) |
| NIFTY | ONE_HOUR | 2026-09-10 | 2026-09-15 | 14 (2 new trading days) |
| BANKNIFTY | FIVE_MINUTE | 2026-09-10 | 2026-09-15 | 147 (2 new trading days) |
| BANKNIFTY | ONE_HOUR | 2026-09-10 | 2026-09-15 | 14 (2 new trading days) |
| SENSEX | FIVE_MINUTE | 2026-09-10 | 2026-09-15 | 148 (2 new trading days) |
| SENSEX | ONE_HOUR | 2026-09-10 | 2026-09-15 | 14 (2 new trading days) |

(Both intervals also confirmed to have a genuine ROLLING retention
window, not unbounded growth: FIVE_MINUTE's oldest available date
advanced from 2026-05-13 to 2026-05-18 and ONE_HOUR's from
2024-09-10 to 2024-09-16 as the newest days were added - expected
broker-side retention behavior, not a bug, and irrelevant to this
investigation since only the NEW forward end matters for OOS
freshness.)

**FRESH_OOS_START_DATE marker, stated explicitly so a future
re-analysis gets this right**: any bar with `timestamp > 2026-09-10
15:30 IST` (the last bar Run 011/012 actually analyzed) is genuinely
fresh - never touched by any tuning, parameter choice, or validation
this project has ever done. Bars at or before that boundary remain
valid for every OTHER purpose but must NOT be counted as fresh OOS
evidence for the Run 011/012 intraday finding specifically.

**Honestly, there is no result to report yet**: only 2 genuinely fresh
trading days exist so far (the market was closed for the weekend in
between). That is far too little to draw any conclusion from - for
comparison, Run 011's SMALLEST individual walk-forward fold alone was
already ~21 trading days. Running any backtest/walk-forward/bootstrap
check against 2 days right now would produce a number, but not a
meaningful one - reporting it would violate this log's own discipline
against manufacturing an impression of validation from too little data.

**Verdict**: infrastructure confirmed working end to end and the
FRESH_OOS_START_DATE boundary is now fixed and documented - the
mechanism for eventually answering Run 011/012's look-ahead-overlap
caveat is in place and requires no further engineering. What remains
is purely a function of calendar time: re-run `python -m
data.pull_history` (locally, or confirm the already-deployed EC2 timer
is doing it automatically) periodically and revisit once at least
~20 fresh trading days have accumulated (a minimally meaningful single
out-of-sample check, matching Run 011's smallest fold) - ideally
closer to 60-90 days before treating a result with the same weight as
Run 011/012's own findings. Not done here, and not worth simulating
early: an honest "not enough data yet" is more useful than a fresh-OOS
number computed from 2 trading days.

**Update (2026-09-16) - a real bug found continuing this same
collection, fixed before it could quietly corrupt anything**: re-ran
`python -m data.pull_history` the next day to keep accumulating fresh
days, this time at ~10:09 IST - DURING market hours, not after close
like the previous run and every actual production run (the deployed
`codex-paper-trading.timer` always fires at 15:45 IST). This surfaced
a genuine gap in `data/historical.py`'s `last_completed_trading_date()`:
despite its name, the function only checked that the market had been
open a few minutes (`>= 9:16 IST`), not that it had actually CLOSED
(15:30 IST) - so it happily returned TODAY as the backfill's `end`
date while today's own trading session was still in progress. Every
interval for all three indices got a still-forming, incomplete "today"
bar silently saved as if it were a real, settled close - exactly the
kind of silent bad-data risk `data/quality.py`'s own stated principle
("never pretend the data exists / is clean if it isn't") exists to
prevent, and check_ohlcv has no way to catch it since an incomplete
bar is still internally OHLC-consistent.

This never mattered in production because the only real caller
(`pull_history.py`) has only ever run at 15:45 IST, always safely
after close - it took running it manually, off the normal schedule,
to expose the gap. **Fixed**: the cutoff is now market CLOSE (15:30
IST), not open; 3 tests updated/added in `tests/test_historical.py`
(including one specifically pinned to the 9:30 IST/noon mid-session
case this incident hit). The already-corrupted local data (18 files -
3 indices x 6 intervals) was cleaned by stripping every row dated
2026-09-16 back out, restoring each file to its last genuinely
complete day (2026-09-15) - confirmed via direct inspection, not
assumed. Fresh-day count is unaffected by any of this: still 2
genuine fresh trading days (2026-09-11, 2026-09-15), since today's
bad data was excluded rather than miscounted. Full suite: 399 passed,
1 skipped (the pre-existing, expected `test_contract_selection.py`
regression skip - unrelated).

---

## Investigation 003 - Validating the stop-vs-target same-bar tie-break assumption

**Date**: 2026-09-18
**Purpose**: event_loop.py's own module docstring has disclosed, since
Run 001, an untested assumption: "If both the stop and target are
breached within the same [daily] bar (a wide-range day), the STOP is
assumed to have been hit first." This has sat as a conservative guess
in every single run in this log - never checkable before, since it
needs to know the real INTRADAY sequence of prices, and this project
had no intraday data until Runs 011/012. Now that real FIVE_MINUTE data
exists, this is finally testable.

**Method**: `backtesting/tie_break_validation.py`. A daily exit is only
"convention-dependent" (the tie-break rule actually changed anything)
when `exit_reason == "stop"` AND that same exit bar's daily high/low
would ALSO have breached target - `_check_stop_target_hit()`'s own
stop-checked-first order guarantees a `"target"` exit_reason could only
ever occur on an unambiguous bar (verified directly, not just asserted,
via `tests/test_tie_break_validation.py`). For each convention-dependent
exit, the plan was to replay the real FIVE_MINUTE bars for that
calendar date to find which was actually breached first - the ground
truth the daily bar alone cannot show.

**Result: zero convention-dependent exits found, across the FULL
5-year history, on all three indices** (95 NIFTY + 97 BANKNIFTY + 94
SENSEX = 286 total closed trades, `BacktestConfig(warmup_bars=30)`,
same config as every other run in this log). Not one single trade, in
five years of history, ever closed on a daily bar that breached both
stop and target simultaneously. This makes the intraday-replay step
moot - there was nothing to check, since the scenario the tie-break
rule exists for never actually occurred with `risk/dynamic_stops.py`'s
current ATR-multiplier-based stop/target widths.

**Verdict: fully resolved, not merely a partial first look (a rare
exception in this log).** Unlike almost every other investigation here,
this doesn't need caveats about a short data window or an unconfirmed
pattern - the check covers the SAME 5-year daily history every prior
Run in this log was computed against, not just the recent
intraday-covered window, and the answer is definitive: the stop-first
tie-break convention has NEVER been the deciding factor in ANY trade's
P&L across every Run and Investigation in this entire log (001-012).
Every prior result stands completely unaffected by this disclosed
assumption, not because the assumption was proven correct, but because
it was never actually invoked. `tie_break_validation.py` remains real,
tested, working infrastructure - if `risk/dynamic_stops.py`'s stop/
target multipliers are ever narrowed in the future (making same-day
double-breaches possible), this module is ready to answer the question
for real using the intraday data that exists by then, including
replaying the actual FIVE_MINUTE sequence rather than just counting
occurrences.

---

## Run 013 - Sequence-risk validation of the REAL compounding equity curve

**Date**: 2026-09-18
**Purpose**: Closes a real, standing gap between Run 003 and Run 008.
Run 003's `monte_carlo_trade_sequence()` reshuffles raw one-conceptual-
unit P&Ls and recomputes a simple cumulative-sum drawdown - valid for
that idealized view, but never applied to `equity_simulation.py`'s REAL
account curve, where reordering trades genuinely changes the OUTCOME,
not just the drawdown path: lot sizing, equity-protection tier gating,
and affordability itself all depend on CURRENT capital, which is
path-dependent under compounding. Run 008's own headline numbers
(+583% to +2144%) were explicitly flagged as "NOT to be trusted... they're
inflated by continuous compounding" - this directly tests that, and
quantifies it for the first time instead of just asserting it.

**Method**: `backtesting/equity_sequence_risk.py`'s
`monte_carlo_equity_sequence()` - reshuffles the ORDER of Run 008's own
already-simulated trades (5,000 reshuffles) and replays the compounding
arithmetic (tier classification, risk-based lot sizing, cost, running
capital) under each new order.

**A real, honest limitation stated up front, not buried**: each
trade's strike/entry premium/exit premium is held FIXED at whatever the
original affordability search chose (which itself depended on the
ORIGINAL capital path) - this test does not re-run strike selection
under each counterfactual capital trajectory, since doing so for 5,000
reshuffles x ~90 trades would be prohibitively slow and isn't necessary
to isolate the specific question asked here: given the SAME set of
priced trades, does the ORDER they occur in change the outcome? It is
an established technique already used elsewhere in this log (Run 003's
own reshuffle test has the identical character - testing a fixed set of
outcomes under different orderings, not re-deriving whether those
outcomes would still occur in a different context).

**Result** (Run 008's exact configuration: 1% risk-per-trade,
affordability-aware strikes, Rs.50,000 starting capital):

| Instrument | n trades | Observed (real order) | Reshuffled mean | Reshuffled 90% CI | Reshuffles worse than observed |
|---|---|---|---|---|---|
| NIFTY | 95 | Rs.381,786 (+663.6%) | Rs.80,187 (+60.4%) | +27.6% to +144.5% | **100.0%** |
| BANKNIFTY | 76 | Rs.382,492 (+665.0%) | Rs.76,137 (+52.3%) | +7.4% to +168.0% | **100.0%** |
| SENSEX | 93 | Rs.491,026 (+882.1%) | Rs.82,225 (+64.4%) | +35.9% to +121.0% | **100.0%** |

**This is one of the most consequential findings in this entire log.**
Every single one of 5,000 random reshuffles, for all three indices,
produced LESS ending capital than what the real historical trade order
actually produced. The real chronological sequence was not a
representative outcome of this trade set - it landed at, or beyond, the
best-case tail of every plausible ordering tried. A "typical" (mean)
reordering of the exact same trades produces roughly +52% to +64%, an
order of magnitude more modest than the +583% to +882% headline numbers
Run 008 reported and explicitly flagged as untrustworthy. This
quantifies exactly what Run 008 could only assert qualitatively: the
headline compounding return is overwhelmingly explained by WHEN the big
winning trades happened to fall in the real sequence (early, so they
compounded into larger position sizes for everything after), not by the
underlying trade-level edge itself.

**Verdict: Run 008's own compounding headline numbers are now
conclusively confirmed as an artifact of favorable sequencing, not a
representative estimate of this strategy's real return distribution.**
The reshuffled mean (+52-64%) - not the observed historical order - is
the honest, decision-relevant number for what this exact set of trades
would typically be expected to produce under compounding. This does not
mean the underlying trades were bad (many other Runs in this log
already established the raw, pre-compounding edge is real if modest,
e.g. Run 002R's walk-forward folds) - it means the SPECIFIC compounding
trajectory reported as Run 008's headline result should never be
quoted or trusted as a representative outcome, only as one lucky
realization out of many. Every future compounding-equity-curve result
in this log (Runs 007-010) should be read with this same caveat unless
separately sequence-risk-tested.

**Update (2026-09-18) - robustness check: does this hold across risk
levels and realistic transaction costs, or is it specific to Run 008's
exact 1% config?** NIFTY only (Run 013 already confirmed near-identical
behavior across all three indices for the base case; matches Run 009's
own precedent of using NIFTY for a detailed parameter sweep).

Risk-level sweep (zero slippage, 3,000 reshuffles each):

| Risk per trade | Observed | Reshuffled mean | Reshuffled 90% CI | Reshuffles worse than observed |
|---|---|---|---|---|
| 0.5% | +352.8% | +40.3% | +20.9% to +101.6% | 100.0% |
| 1.0% | +663.6% | +60.6% | +27.6% to +152.0% | 100.0% |
| 2.0% | +837.9% | +74.4% | +43.5% to +152.2% | 100.0% |
| 5.0% | +801.5% | +218.0% | +40.8% to +707.2% | 96.3% |

Tick-slippage sweep (fixed 1% risk, Run 009's own tested tick levels,
3,000 reshuffles each):

| Tick spread (round-trip) | Observed | Reshuffled mean | Reshuffles worse than observed |
|---|---|---|---|
| Rs.0.00 | +663.6% | +60.6% | 100.0% |
| Rs.1.00 | +663.0% | +60.6% | 100.0% |
| Rs.2.50 | +662.0% | +60.5% | 100.0% |
| Rs.5.00 | +660.5% | +60.3% | 100.0% |

**The finding is robust, not an artifact of one specific configuration.**
Sequence risk stays essentially total (100% of reshuffles worse) from
0.5% through 2% risk-per-trade, and remains dominant even at 5% risk
(96.3%) - though the reshuffled distribution widens dramatically there
(CI span 40.8% to 707.2%, vs. a much tighter spread at lower risk),
consistent with equity-protection tier gating and position sizing
interacting more chaotically with trade order once risk-per-trade gets
aggressive. Realistic transaction costs change almost nothing: the
tick-slippage sweep is nearly IDENTICAL across the full Rs.0-5/unit
range Run 009 tested, since a per-trade slippage cost is a small,
roughly uniform drag regardless of sequencing - it doesn't change WHICH
order is favorable, only shaves a small, consistent amount off every
outcome. Confirms this isn't a fragile, config-specific result: any
reasonable risk level or slippage assumption in this log's own tested
range shows the same conclusion.

**Correction (2026-09-18) - a real bug found while investigating
Experiment 006's BANKNIFTY divergence (models/EXPERIMENTS.md), verified
against the live scrip master, not assumed from memory**: BANKNIFTY's
real near-the-money strike spacing is Rs.100, NOT Rs.50 - confirmed by
querying the live instrument list directly (`diffs seen: [100.0, ...]`
for BANKNIFTY vs `[50.0, ...]` for NIFTY and `[100.0, ...]` for SENSEX).
The BANKNIFTY row in this Run's own result table above used
`strike_increment=50` - the wrong value. NIFTY (50) and SENSEX (100)
were both already correct.

Re-run with the corrected value (BANKNIFTY, 1% risk, 5,000 reshuffles):
n trades simulated changes from 76 to **94** (the coarser, correct real
spacing made more strikes affordable, not fewer), observed ending
capital Rs.343,138 (+586.3%, close to the original +665.0%), but
**reshuffled mean jumps to +296.3%** (vs. the wrong value's +52.3%) and
**fraction of reshuffles worse than observed drops to 86.1%** (vs. the
wrong value's misleadingly clean 100.0%) - a materially different
number, not a rounding-level correction. The qualitative conclusion
(the real order is meaningfully better than a typical reshuffle) still
holds for BANKNIFTY, just less starkly than originally reported - the
corrected BANKNIFTY row should read: **n=94, observed +586.3%,
reshuffled mean +296.3%, reshuffles worse than observed 86.1%**,
replacing the wrong 76/+665.0%/+52.3%/100.0% values above.

**Scope of what this correction covers, stated precisely**: this fixes
Experiment 006 and this Run's own BANKNIFTY numbers, both computed
directly in this session with a verified-wrong `strike_increment=50`.

**Resolved (2026-09-18, see Run 008's own update)**: re-verified Run
008's original BANKNIFTY figures directly, using confirmed-correct
strike increments against current data. Headline and worst-fold values
matched the original very closely (BANKNIFTY -11.9% vs -12.0%, +586.3%
vs +584%) - strong evidence Run 008's original numbers already used the
correct Rs.100 spacing. A control check (re-verifying NIFTY too, whose
strike_increment never changed) showed similar-sized fold-level drift
purely from the underlying data's rolling window having moved forward
several days since Run 008 was first computed - confirming the earlier
mismatch was data drift, not the same bug. Stated with appropriate
hedging (the exact original code isn't available to confirm with
certainty), but no longer an open question - see Run 008's update for
the full comparison table.

---

## Run 014 - Portfolio-level equity simulation: one shared capital pool, not three

**Date**: 2026-09-18
**Purpose**: Closes the biggest remaining structural gap in this
backtesting system. Every Run through 013 treats NIFTY/BANKNIFTY/
SENSEX as three INDEPENDENT Rs.50,000 accounts, each with its own
capital, tiers, and affordability search. A real account is ONE pool
of capital all three instruments' signals compete for simultaneously -
portfolio/greek_aggregation.py's concentration check was built for
exactly this scenario but has never been wired into an actual,
capital-constrained backtest until now.

**Method**: `backtesting/portfolio_equity_simulation.py`'s
`simulate_portfolio_equity_curve()`. Deliberately reuses each
instrument's entry/exit TIMING decisions UNCHANGED from independently-
run `backtesting.event_loop.run_backtest()` calls, exactly as every
prior Run already does - Investigation 001/Run 004 established strike
choice (and by extension, capital availability) is orthogonal to entry
timing, so there's no need to re-decide WHEN to trade, only whether a
shared, finite pool of capital can afford a trade the moment it wants
to enter, given what OTHER instruments' currently-open positions may
already have locked up. Verified directly (not assumed) that NIFTY/
BANKNIFTY/SENSEX's real ONE_DAY data shares the EXACT same 1,241-day
trading calendar, so trades from all three merge into one true
chronological event stream by row index alone, no date-reindexing
needed.

**The key new mechanic, absent from every prior Run because it never
mattered with only one instrument at a time**: a bought option's
premium is paid in CASH at entry and only returned (plus/minus P&L) at
exit. With genuine concurrency, three simultaneously-open positions
really do lock up real cash a fourth signal cannot spend twice - this
module tracks free cash explicitly, unlike equity_simulation.py's
single-instrument loop, which only ever needed "current capital" and
"free cash" to be the same number.

**Honest limitation, stated up front**: equity-protection tier
classification and risk budgeting use FREE CASH, not mark-to-market
portfolio equity (free cash + current value of open positions) - a
conservative simplification that understates true equity while
positions are open, chosen because repricing every open position at
every event would add real complexity without changing the qualitative
question this Run exists to answer.

**Correctness check, not just a result**: with only one instrument
populated, the shared-cash accounting reduces to EXACTLY the same
capital trajectory as the existing single-instrument
`simulate_equity_curve_with_affordable_contracts()` - verified as an
exact numeric match (`tests/test_portfolio_equity_simulation.py`), not
an approximation, since entry_cost is deducted at entry and refunded in
full (plus/minus net P&L) at exit, which algebraically nets to the same
"+= net_pnl" the single-instrument version does directly.

**Result** (1% risk-per-trade, real lot sizes/strike increments, full
5-year history):

| | Trades | Skipped | Ending capital | Return | Starting capital |
|---|---|---|---|---|---|
| NIFTY (independent) | 95 | 0 | Rs.381,786 | +663.6% | Rs.50,000 |
| BANKNIFTY (independent) | 94 | 3 | Rs.343,138 | +586.3% | Rs.50,000 |
| SENSEX (independent) | 93 | 1 | Rs.491,026 | +882.1% | Rs.50,000 |
| **Three independent pools, combined** | **282** | **4** | **Rs.1,215,950** | **+710.6%** | **Rs.150,000 total** |
| **Portfolio (one shared pool)** | **286** | **0** | **Rs.1,182,479** | **+2,265.0%** | **Rs.50,000 total** |

Max concurrent positions in the portfolio run: **3** (all three
instruments genuinely held positions simultaneously at least once) -
confirming real concurrency actually occurs with this trade set, not
just a theoretical possibility.

**Two genuine, non-obvious findings, not assumed ahead of time**:
1. The portfolio afforded MORE total trades (286 vs 282) than the sum
   of three independent pools, including recovering all 4 trades any
   independent pool had to skip as unaffordable. Pooled capital isn't
   simply "more constrained" by concurrency (which is real - 3
   simultaneous positions did occur) - it also compounds FASTER in
   aggregate, since a gain in any one instrument boosts capital
   available to all three, not just its own instrument's separate,
   smaller pool. Which effect dominates was not obvious ahead of time
   and isn't a general law - it's an empirical property of this
   specific trade set and risk level.
2. Despite genuine 3-way concurrency, ZERO trades were skipped for lack
   of cash in the portfolio run. By the time real overlap occurred, the
   shared pool had already compounded large enough that cash
   contention, while structurally real (the mechanic this Run exists to
   model), never actually bound in practice for this specific
   historical run and risk level.

**The capital-efficiency case, stated plainly**: the portfolio reaches
nearly the SAME absolute ending capital (Rs.1,182,479 vs Rs.1,215,950,
97% as much) starting from ONE THIRD the total capital (Rs.50,000 vs
Rs.150,000) - a genuinely different, and more realistic, picture of
what this trade set can do with the spec's own stated Rs.50,000
starting capital than any single-instrument Run in this log has shown.

**The same sequence-risk caveat Run 013 established applies here too,
likely even more so - NOT independently re-tested in this Run**: this
is a single, continuously-compounding, non-independent-fold headline
number, exactly the kind Run 013 already proved is frequently dominated
by favorable trade-order sequencing rather than real edge. Pooling
three instruments' trades into one shared, compounding trajectory
plausibly AMPLIFIES this same risk (more trades sharing one compounding
path means more opportunities for early-sequence luck to compound
through the whole curve) - not diminishes it. The +2,265.0% figure
above must NOT be read as validated or even directionally reliable
until sequence-risk tested the same way Run 013 tested the single-
instrument case. This is the concrete, explicitly flagged next step,
not done here.

**Verdict**: the structural capability this log has been missing since
Run 001 now exists and is verified correct (exact match in the
degenerate single-instrument case, genuine concurrency confirmed in the
real multi-instrument case). The capital-efficiency finding (comparable
absolute return from a third of the capital) is a real, structurally-
grounded result, not a fluke of pricing - but the specific headline
percentage carries the same well-established sequence-risk caveat as
every other compounding-equity-curve number in this log, and that
caveat has NOT yet been separately verified for the portfolio case.
Report the mechanism and the capital-efficiency insight; do not quote
the +2,265.0% headline as a validated return.

---

## Run 015 - Sequence-risk validation of the portfolio equity curve

**Date**: 2026-09-18
**Purpose**: The concrete next step Run 014 explicitly flagged - its
pooled headline return (+2,265.0%) carries the same sequence-risk
caveat Run 013 proved dominates single-instrument compounding curves,
plausibly amplified by pooling. This tests that directly.

**Why Run 013's own reshuffle doesn't extend directly**: Run 013
reorders a single, strictly sequential list of non-overlapping trades -
valid because one instrument never has two open positions at once. A
portfolio has THREE such sequential streams (each individually
non-overlapping, per event_loop.py's own rule) that genuinely overlap
IN TIME with each other - that concurrency is the entire subject of Run
014. Reshuffling "all trades" as one flat list without preserving each
instrument's own internal order would create impossible same-instrument
overlaps and erase the very thing being tested.

**Method, stated precisely**: `backtesting/portfolio_sequence_risk.py`.
Riffle-shuffles the INTERLEAVING of the three instruments' own (real,
historically-ordered) trade sequences - like shuffling three already-
sorted decks together: each deck's internal order is preserved exactly,
only their pacing relative to each other is randomized. Held fixed:
each instrument's own relative trade order, and every trade's real
entry_premium/exit_premium/strike/duration (priced once from real
historical data). Varied: the logical arrival time of each instrument's
trades relative to the other two, which determines what overlaps happen
and when, and therefore how capital gets allocated. Real calendar dates
are abandoned for a logical timeline (duration in bars is preserved,
WHEN a sequence starts is what's randomized) - the same abstraction
Run 013's own reshuffle already relies on.

**Result** (Run 014's exact portfolio configuration, 5,000 reshuffles):

| | Value |
|---|---|
| Observed (real interleaving) | +2,265.0% |
| Reshuffled mean | **-3.9%** |
| Reshuffled 90% CI | -8.5% to +20.9% |
| Reshuffles worse than observed | **100.0%** |

**This is more extreme than Run 013's single-instrument finding, not
just consistent with it.** Run 013 found NIFTY/SENSEX/BANKNIFTY's
INDIVIDUAL historical orderings were already near the best-case tail of
their own possible sequences (reshuffled means +52-64%, still solidly
positive). Here, the reshuffled MEAN is NEGATIVE - a typical
interleaving of these same three real trade sequences would produce a
roughly break-even-to-small-loss outcome, not the reported +2,265.0%
gain. This makes sense once stated plainly: the portfolio's headline
combines THREE instruments' individually-already-lucky historical
orderings into one shared, compounding timeline - stacking favorable
sequencing three times over, rather than diluting it. Confirms
precisely what Run 014's own writeup warned was plausible but unproven:
pooling doesn't just carry single-instrument sequence risk, it
amplifies it substantially.

**Verdict: Run 014's pooled headline return is now conclusively
confirmed as an extreme, non-representative outcome, more so than any
single-instrument compounding number in this entire log.** The
reshuffled mean (essentially flat, -3.9%) - not the observed +2,265.0% -
is the honest, decision-relevant estimate of what this exact set of
real, historically-priced trades would typically produce if pooled
under a different, equally plausible interleaving. This does not undo
Run 014's real, structural finding (genuine 3-way concurrency occurs,
and portfolio pooling is a more realistic model of an actual single
account than three artificial separate pools) - it means the SPECIFIC
compounding trajectory Run 014 reported must never be quoted or trusted
as a representative outcome, on the same footing as Run 008's own
now-well-understood headline number, only more so. The capital-
efficiency insight (comparable outcome from a third of the capital)
remains worth investigating further, but not via this specific
historical interleaving's percentage return.

---

## Run 016 - Was the capital-efficiency finding itself just two lucky draws?

**Date**: 2026-09-18
**Purpose**: Direct follow-up to Run 015's own flagged item. Run 014's
"capital efficiency" claim (the pooled account reaches ~97% of three
independent pools' combined ending capital using 1/3 the capital) was
built by comparing the pooled run's OBSERVED (real historical) outcome
against the independent pools' OWN observed outcomes - but Run 013
already proved each independent pool's observed outcome is itself
near the lucky tail of its own possible orderings, and Run 015 just
proved the pooled outcome is even more so. Comparing two lucky draws
against each other proves nothing about a real structural advantage -
this checks whether the SAME comparison holds under TYPICAL (reshuffled
mean) conditions instead of real historical luck on both sides.

**Method**: no new code - a direct, apples-to-apples recombination of
numbers already computed and logged in Run 013 (each instrument's own
`monte_carlo_equity_sequence()` reshuffled mean, independently) and
Run 015 (the portfolio's own `monte_carlo_portfolio_sequence()`
reshuffled mean), both expressed as % return on their own starting
capital so the comparison isn't distorted by the 3x difference in
total capital deployed.

**Result**:

| | Starting capital | Observed (lucky) | Reshuffled mean (typical) |
|---|---|---|---|
| NIFTY (independent) | Rs.50,000 | Rs.381,786 | Rs.80,187 |
| BANKNIFTY (independent) | Rs.50,000 | Rs.343,138 | Rs.198,170 |
| SENSEX (independent) | Rs.50,000 | Rs.491,026 | Rs.82,225 |
| **3 independent pools, combined** | **Rs.150,000** | **Rs.1,215,950 (+710.6%)** | **Rs.360,582 (+140.4%)** |
| **Portfolio (pooled)** | **Rs.50,000** | **Rs.1,182,479 (+2,265.0%)** | **Rs.48,050 (-3.9%)** |

**The finding reverses completely once compared fairly.** Under the
observed, lucky historical draws, pooling looked like it captured
almost the same absolute gain from a third of the capital (Run 014's
headline). Under TYPICAL conditions (reshuffled mean, the honest number
per Run 013/015's own established discipline), pooling looks
dramatically WORSE: -3.9% pooled vs. +140.4% combined for three
independent pools. The original "capital efficiency" finding was itself
an artifact of comparing two different lucky draws against each other,
not a real structural property of pooling.

**A real, structural mechanism explains this, not just noise**: sharing
ONE capital pool means a bad early stretch in ANY single instrument (in
a given reshuffled interleaving) triggers `risk/equity_protection.py`'s
drawdown-based tier downgrade for the WHOLE shared pool - reducing the
risk budget for ALL THREE instruments' subsequent trades, not just the
one that lost. With three separate pools, a bad stretch in one
instrument's own sequence only downgrades that instrument's own tier,
leaving the other two sized normally against their own, undamaged
capital. Pooling ties the instruments' fates together through the
SIZING/TIER mechanism itself, even though their underlying price moves
may be far less correlated - a genuine structural cost of a naive
shared-capital-with-shared-tier design, not an artifact of this
specific test.

**Verdict: Run 014's capital-efficiency finding does NOT survive a fair
comparison and should be considered retracted, not merely caveated.**
This is a rare case in this log of a finding built on real code and
real data still being wrong once examined more carefully - not because
of a bug, but because the comparison itself (lucky vs. lucky) was the
wrong one to draw a structural conclusion from. Under the honest,
reshuffled-mean view, this specific pooled configuration (1% risk,
shared drawdown-based tiers) looks WORSE than three independent
Rs.50,000 pools, not better. This does not mean pooling capital across
instruments is inherently a bad idea - it means THIS mechanism for
doing it (one shared equity-protection tier gating all three
instruments together) transmits single-instrument bad luck across the
whole account in a way three separate pools would not. A genuinely
different pooling design - e.g., per-instrument risk budgets sized off
a shared pool without a single shared drawdown-tier gate, or explicit
portfolio-level Greek concentration limits (portfolio/greek_aggregation.py,
still never actually used to BLOCK a trade in any Run in this log) -
might behave differently, but that is new, unbuilt work, not a
re-reading of what exists today.

---

## Run 017 - Wiring in the portfolio Greek-concentration limit (first real use)

**Date**: 2026-09-18
**Purpose**: Directly closes the gap Run 016 named as genuinely
different, unbuilt future work: portfolio/greek_aggregation.py's
concentration check has existed since early in this project but has
never actually been used to BLOCK a trade in any Run in this log. Wires
it into the portfolio equity simulation as a real, first use.

**Method**: `backtesting/portfolio_equity_simulation.py`'s
`simulate_portfolio_equity_curve()` gained a new
`max_single_instrument_delta_share` parameter (default `None`,
preserving every prior Run's exact behavior - verified as an exact
regression match in `tests/test_portfolio_equity_simulation.py`). When
set, a candidate entry is skipped if it would push one instrument's
share of TOTAL portfolio delta above the given fraction - checked
against every currently-OPEN position's Greeks REPRICED as of the
candidate's own entry date (not their own stale entry-time Greeks,
which would understate how exposure has actually moved since). Only
the relative concentration SHARE is checked, deliberately: the absolute
delta/gamma/vega/theta limits check_portfolio_risk() also supports
would need an arbitrary, unjustified numeric threshold to be picked - a
relative share needs none.

**Result** (1% risk, same historical trade set as Run 014):

| Concentration cap | Trades taken | By instrument | Skipped | Ending capital | Max concurrent | Max drawdown |
|---|---|---|---|---|---|---|
| None (Run 014 baseline) | 286 | BANKNIFTY 97 / NIFTY 95 / SENSEX 94 | 0 | Rs.1,182,479 (+2,265.0%) | 3 | 21.0% |
| 60% | 123 | BANKNIFTY 39 / NIFTY 63 / SENSEX 21 | 163 | Rs.518,016 (+936.0%) | 3 | 11.7% |
| 40% | 109 | BANKNIFTY 39 / NIFTY 62 / SENSEX 8 | 177 | Rs.485,533 (+871.1%) | 1 | 13.7% |

**What this does and does NOT establish, stated precisely**: the
mechanism works exactly as designed - a real, previously-inert risk
control now genuinely gates trades (skipping 163-177 of the ~449
candidates it was checked against), visibly shifts the resulting trade
mix away from instruments that would otherwise dominate concentration
(SENSEX drops from 94 to 8 trades under the 40% cap), and reduces the
OBSERVED maximum drawdown (21.0% to 11.7-13.7%). This IS a genuine,
useful finding on its own: the concentration check, wired in for the
first time, functions correctly against real data, not just in
isolated unit tests.

**What it does NOT establish**: whether concentration limits improve
the TYPICAL (not just this one observed, historically-lucky) outcome.
Every ending-capital and drawdown number above is drawn from the SAME
single historical trade sequence Run 013/015/016 already proved is an
extreme, non-representative draw for this trade set - properly
answering "does concentration-limiting help under typical conditions"
would need extending Run 015's reshuffle methodology to this
Greek-repricing-gated version too. Deliberately NOT done here: each
reshuffle would need real per-entry Greek repricing against every open
position (not just cash arithmetic), making it substantially more
expensive computationally than Run 015's own reshuffle - a real,
scoped-out next step, not a shortcut taken quietly.

**Verdict**: a real capability gap closed (the concentration check is
no longer inert infrastructure), and a real, honestly-scoped mechanism
finding (it works, and visibly changes trade selection and observed
drawdown in the expected direction) - but reported as exactly that, not
inflated into a claim about typical-case risk-adjusted performance that
would need the more expensive reshuffle test this Run explicitly does
not attempt.

---

## Run 018 - Does concentration-limiting help under TYPICAL conditions?

**Date**: 2026-09-18
**Purpose**: Directly closes the more expensive reshuffle test Run 017
explicitly scoped out - does the concentration limit improve typical
(reshuffled-mean) performance, or only the one observed, already-known-
lucky historical draw?

**The real technical obstacle, and how it was resolved**: Run 015's
logical-timeline reshuffle has no real calendar dates, so Run 017's
approach (reprice every open position's CURRENT Greeks as of the new
candidate's real date) has nothing to reprice against. Resolution: a
trade's own ENTRY-TIME Greeks are reshuffle-INVARIANT - they only
depend on its real, unchanging entry_index/strike/expiry, never on
where it lands in a given reshuffle - so they can be priced ONCE up
front (before the 5,000-reshuffle loop) and reused cheaply, using
entry-time Greeks as a stable proxy for "current" exposure rather than
Run 017's fresher, real-calendar repricing. Stated as an approximation,
not a silent difference from Run 017's own method.

**Result** (`backtesting/portfolio_sequence_risk.py`'s extended
`monte_carlo_portfolio_sequence()`, 5,000 reshuffles each):

| | Observed | Reshuffled mean | Reshuffled 90% CI | Reshuffles worse than observed |
|---|---|---|---|---|
| No limit (Run 015 baseline, reproduced exactly) | +2,265.0% | -3.9% | -8.5% to +20.9% | 100.0% |
| 40% concentration cap (Run 017 config) | +871.1% | -2.2% | **-4.0% to -1.8%** | 100.0% |

**A genuine, nuanced finding - not a fix, but not nothing either.**
Concentration-limiting does NOT turn this strategy's typical outcome
positive: both configurations' reshuffled means stay solidly negative,
and 100% of reshuffles still beat the observed headline either way -
Run 016's core conclusion (the shared-pool mechanism's typical outcome
is poor, not just its lucky-draw headline) is NOT overturned by adding
a concentration limit. What DOES change substantially: the reshuffled
distribution's WIDTH collapses from a 29.4-percentage-point 90% CI span
(-8.5% to +20.9%) to a 2.2-point span (-4.0% to -1.8%) - an order of
magnitude tighter. The concentration limit doesn't fix the expected
value, but it substantially reduces how much that value varies across
plausible orderings - a real, distinct risk-management property
(consistency/predictability) from the one it fails to fix (typical
return).

**Verdict**: closes the loop Run 017 opened, honestly. Wiring in the
concentration check (Run 017) was a real capability gain, and this Run
proves its benefit is real but narrower than a first glance might
suggest - it buys predictability, not profitability, for this specific
trade set and risk configuration. Combined with Run 016's diagnosis
(the shared drawdown-tier mechanism, not concentration per se, is what
transmits one instrument's bad luck to the others), this points clearly
at where future portfolio-design work would need to focus: the SIZING/
TIER mechanism Run 016 identified, not the concentration dimension Run
017/018 have now thoroughly explored. That specific redesign remains
real, unbuilt future work.

---

## Run 019 - Per-instrument tiers: the redesign Run 016 named as unbuilt future work

**Date**: 2026-09-18
**Purpose**: Builds the specific alternative Run 016/018 pointed at -
per-instrument risk budgets sized off a shared pool, WITHOUT one shared
drawdown-tier gate dragging all three instruments' sizing down together.

**Method**: `backtesting/portfolio_equity_simulation.py`'s new
`simulate_portfolio_equity_curve_per_instrument_tiers()`. Keeps the
SAME real, shared CASH pool for affordability (the genuine, hard
constraint this whole portfolio line of work exists to model), but
tracks a separate NOTIONAL capital balance per instrument - an equal
split of starting capital across however many instruments are present
- used ONLY for that instrument's OWN tier classification and risk-
budget sizing. Realized P&L updates both the real shared cash (what
ending_capital/drawdown are computed from) and that instrument's own
notional balance (what decides ITS future sizing only). Correctness
verified two ways: an exact match with the existing single-instrument
simulation when only one instrument is present (same argument as Run
014's own regression test), and a targeted synthetic test proving a
massive loss in one instrument does NOT reduce another's position size,
unlike the shared-tier design where it does (confirmed directly: the
shared design's own STOP-tier gate, `risk/equity_protection.py`'s -15%
threshold, fully blocks the second instrument's trade after the first's
loss; the per-instrument design sizes it normally).

**`backtesting/portfolio_sequence_risk.py` extended to match**:
`replay_portfolio_in_logical_order()`/`monte_carlo_portfolio_sequence()`
gained a `per_instrument_tiers` flag mirroring the same notional-vs-
shared split, so the reshuffle test could properly evaluate this design
too, not just its one observed historical draw.

**Result** (1% risk, same historical trade set, 5,000 reshuffles):

| | Observed | Reshuffled mean | Reshuffled 90% CI | Reshuffles worse than observed |
|---|---|---|---|---|
| Shared tier (Run 014/015 baseline) | +2,265.0% | -3.9% | -8.5% to +20.9% | 100.0% |
| **Per-instrument tiers (this Run)** | +1,486.0% | **+1.9%** | -5.7% to +19.1% | 100.0% |

**The first positive typical outcome in this entire portfolio
investigation arc.** Every prior compounding result in this log,
single-instrument or pooled, has had a reshuffled/typical mean at or
below zero once properly sequence-risk-tested (Run 013's individual
instruments were the closest exceptions, all still well above zero
before pooling wiped that out in Run 015). Per-instrument tiers flips
the pooled design's typical outcome from solidly negative to
marginally positive - confirming Run 016's diagnosis directly: it WAS
specifically the shared drawdown-tier mechanism transmitting bad luck
across instruments that made the original pooled design's typical
outcome poor, not pooling or concurrency themselves.

**Still not claimed as validated or strongly positive, stated
honestly**: +1.9% is a modest, close-to-flat mean, well within a wide
CI that still spans meaningfully negative territory (-5.7%) - this is
"no longer clearly a loser," not "a proven edge." 100% of reshuffles
still beat this Run's own +1,486.0% observed headline, exactly the
same caveat every compounding number in this log carries - the
observed historical draw remains untrustworthy on its own even under
this improved design.

**Verdict**: the specific, genuinely different pooling design Run
016/018 named as real, unbuilt future work has now been built and
tested, and it does what the diagnosis predicted - decoupling sizing
from a single shared drawdown gate measurably and directly improves
the typical outcome, turning a solid loser into a marginal, close-to-
breakeven result. This completes the portfolio-design investigation
arc (014-019) at a genuinely informative endpoint: real concurrency
exists and matters (014), naive pooling's headline is sequence-luck
(015), naive pooling's typical outcome is actually worse than separate
pools (016), concentration limits alone don't fix that (017-018), and
decoupling the sizing mechanism itself does meaningfully help, though
not enough on its own to call this a validated edge (019).

---

## Run 020 - Walk-forward validation: the picture reverses, and that's the finding

**Date**: 2026-09-18
**Purpose**: Every portfolio Run so far (014-019) used only two
validation lenses - full-history observed, and reshuffle/sequence-risk.
No portfolio result has been walk-forward validated (independent,
non-overlapping folds, fresh capital per fold) the way single-
instrument Runs (002R, 006, 008) always are. Checks whether Run 019's
promising reshuffled-mean result holds up under this third, independent
lens too - reusing the SAME fold-splitting methodology already verified
correct against Run 008's own numbers (filter each instrument's
already-decided trades by entry_index range, run fresh per fold).

**Result** (same 5-fold windows as Run 002R/006/008, 1% risk):

| Fold | Shared-tier | Per-instrument tiers |
|---|---|---|
| [0:244] | +337.5% | +70.8% |
| [249:493] | +356.6% | +86.0% |
| [498:742] | +271.1% | +126.0% |
| [747:991] | +409.9% | +42.5% |
| [996:1240] | +3.2% | **-7.1%** |
| **Folds profitable** | **5/5** | **4/5** |

**This REVERSES Run 019's own reshuffle ranking, and that reversal is
itself the real finding, not a contradiction to explain away.** Run
019 found per-instrument tiers has a better TYPICAL (reshuffled-mean)
outcome over the full 5-year compounding horizon. Here, under
short, independently-capitalized folds, shared-tier wins on every
measure - more folds profitable, higher returns in every single fold.
The mechanism is real and traceable, not a fluke of one specific split:
per-instrument tiers structurally CAPS each instrument at sizing
against only its own 1/3 notional share of capital, even during a
GOOD stretch for that instrument - it can never access the other two
instruments' unused capital to size up further. Shared-tier has no
such cap: a hot streak in any one instrument gets to use the FULL
pool. Over a short, fresh-capital fold, this uncapped upside dominates;
over a long compounding horizon with adverse reshuffled orderings, the
same lack of a cap is what let one instrument's bad luck drag the
whole pool down (Run 016's original diagnosis). The two lenses are not
disagreeing about facts - they are stress-testing genuinely different
failure/success modes of the SAME structural trade-off.

**Verdict: there is no single "better" design between these two -
which one wins depends on the question being asked.** Per-instrument
tiers protects against long-horizon, one-instrument-drags-down-the-
account risk (what Run 016 diagnosed and Run 019 confirmed fixing) at
the direct cost of capping upside during favorable, short-to-medium
stretches (what this Run now shows). Reporting both honestly rather
than picking the flattering lens: a real production design would need
to decide which failure mode it cares more about avoiding - not
something this backtesting log can decide on its own, and not
attempted here. This closes out the 014-020 portfolio investigation
arc with an honest, mechanistic understanding of the real trade-off
involved, rather than a false "solved it" conclusion from Run 019
alone.
