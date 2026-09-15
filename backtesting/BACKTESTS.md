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
