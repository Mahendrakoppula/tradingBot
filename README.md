# Codex — Autonomous ML-Driven Index Options Trading System

Status: **Phase 9, 14, and 15 done, Phase 17/18 structural backbone done, global/derivatives/news context engines done, Phase 7 in progress** (of 18). **IMPORTANT: Run 007 (backtesting/BACKTESTS.md) found the spec's own Rs.50,000 capital cannot afford even one lot of NIFTY/BANKNIFTY/SENSEX options at ATM at a conservative risk-per-trade percentage, given real current lot sizes/premiums. Run 008's affordability-aware strike selection is a genuine partial fix (Rs.50,000 CAN now participate, far-OTM), but its own headline returns are explicitly flagged as untrustworthy (inflated by continuous compounding) - read both entries before assuming this capital figure is workable as stated. Run 013 QUANTIFIES exactly how untrustworthy: reshuffling the order of Run 008's own trades 5,000 times, the large majority of reshuffles (86-100%, per instrument) produced less ending capital than the real historical order - the reshuffled mean (NIFTY/SENSEX +60-64%, BANKNIFTY +296% after a strike-spacing bug fix - see Run 013's own correction note) is well below Run 008's headline (+583-882%), which sits toward the best-case tail of possible orderings, not a representative outcome.** (Phase 1: scaffolding/config/
logging/notifications/deploy - done. Phase 2: historical OHLCV data
pipeline + quality engine - done, and real data has now actually been
pulled (data/raw/, gitignored - see "Local development" below to
refresh it). Phase 3: pre-indicator market-state engine - done. Phase 4:
multi-timeframe fusion - done. Phase 5: theoretical options/Greeks
engine - done, spot-proxy only, see features/theoretical_options.py for
the explicit, permanent limitations of this data source. Phase 6:
strategy framework - done, 3 of the spec's ~10 strategy types built as a
starting portfolio, see strategies/ below - more can be added to the
same framework without a new phase. Risk engine (daily P&L selectivity,
equity-protection capital tiers, position sizing) - done, see risk/
below. Phase 7: ML models - Model 1/8 (regime classifier) built and
evaluated against real data; it did NOT beat the naive baseline, and a
subsequent 8-fold walk-forward validation confirmed this much more
conclusively (beat baseline in only 2 of 24 fold-instrument evaluations) -
see models/EXPERIMENTS.md for both honest results and why neither was
tuned to look better. Dynamic ATR/structure stops, portfolio Greek aggregation,
an execution engine (always simulated), Phase 12's event-driven
backtester, and Phase 13's walk-forward + bootstrap/Monte Carlo
validation are all done too - see backtesting/BACKTESTS.md, including a
real bug it caught (the daily risk engine never reset between bars,
silently freezing BANKNIFTY's entire backtest after one early bad
stretch) and the corrected, genuinely mixed result after fixing it:
bootstrap confidence intervals exclude zero on all three indices, but
walk-forward still shows only 3/5 time-blocked folds profitable -
suggestive, not proof of a validated edge. Phase 9 (trade ranking +
contract selection) and Phase 15 (Streamlit dashboard) are also done -
see strategies/ranking.py, strategies/contract_selection.py, and
dashboard/ below. Phase 14 (paper trading) is code-complete and tested
(paper_trading/) - a once-daily loop, run near market close, that
reuses backtesting/event_loop.py's exact process_bar() logic against
live-refreshed data and persists state/sends Telegram notifications for
PAPER trades only (the module never even imports execution/order_client.py -
structurally incapable of a real order, not just gated by a flag).
Scheduled via deploy/codex-paper-trading.service+.timer, Mon-Fri 15:45
IST (15 min after market close) - refreshes data then runs the decision
loop, on the same shared instance as the other two bots.). No live
trading logic exists yet - nothing here is
promoted or wired into a live decision.

**Follow-up investigation** (backtesting/attribution.py, BACKTESTS.md's
Investigation 001): traced the walk-forward inconsistency to a specific,
mechanistically-understood cause - trend_following losing at a real
Nov 2025-Mar 2026 market reversal (a well-known trend-following
characteristic, not a bug), confirmed across all three indices though
they're correlated enough that this is really one market event, not
three independent confirmations. Deliberately not "fixed" - doing so
would mean fitting a rule to the one reversal visible in this dataset.

**Infrastructure**: runs on the SAME shared t3.micro EC2 instance as the
existing `main`-branch bots (not new/dedicated infra) - as a third,
independent systemd service (`codex-trading.service`) in its own
`/opt/codex-trading` directory and Python venv, no Docker/database on
this box. That instance has only 1GB RAM and runs two other bots with
real money live on it, so Phase 1 deliberately has no database
dependency (`database_enabled=False` by default) until a later phase
actually needs persistent storage, at which point instance sizing gets
reassessed.

This branch (`codex-bot-main`) is a deliberately separate root - no
shared history with `main`, which holds the existing `run_daily.py`/
`run_technical.py` bots. The two are independent systems sharing only an
AWS account and Angel One SmartAPI credentials; nothing here imports from
or depends on the code on `main`.

## Origin and scope

Built from an 83-section master spec (regime/market-state engines,
multi-timeframe fusion, options/futures/Greeks/volatility intelligence,
NLP/macro event engines, 8 specialized ML models with meta-labeling and
calibration, a leakage-audited event-driven backtester, walk-forward/
Monte Carlo validation, autonomous model promotion under hard
non-overridable risk limits, a dashboard, and a Dockerized AWS
deployment). The spec's own Section 80 requires building this in 18
incremental phases - see `.claude/plans/async-jumping-karp.md` (the plan
this Phase 1 was built from) for the full phase breakdown and the
reasoning behind every infrastructure choice below.

**Known, inherited limitation**: SmartAPI has no historical premium data
for expired option contracts (confirmed on the `main`-branch bots this
account already runs). Every options-related phase here will use the same
spot-price-proxy convention already established in that project's own
research area - real spot data, approximated/skipped option Greeks where
real history doesn't exist. Not solved here, just carried forward
honestly.

**Confirmed data limitation (found in this project, Phase 7)**: SmartAPI
reports index spot volume as 0, always, for all three in-scope indices
at every interval down to ONE_MINUTE - indices aren't a traded security
with their own volume the way a stock is. Every volume-derived signal
(market_state/liquidity.py, any relative-volume ML feature) is
permanently INSUFFICIENT_DATA/excluded for this reason, not a bug or a
"not implemented yet".

## Risk parameters (locked in from the spec)

- Starting capital: Rs.50,000
- Hard maximum daily loss: Rs.2,000 (non-overridable kill switch, not an
  ML-adjustable parameter)
- Rs.800-1,000 daily P&L is a **selectivity threshold**, not a stop
  target and not an individual trade's stop-loss - see `config/settings.py`
- Individual trade stops/targets are computed dynamically from market
  structure/volatility, never hardcoded to a fixed rupee amount

## Directory layout

```
config/       centralized settings (Pydantic), env-var driven
monitoring/   structured logging, Telegram notifications, health checks
app/          entry point(s) - currently a health-check skeleton only
data/         (Phase 2, done) SmartAPI historical OHLCV fetch, quality
              engine, Parquet storage - index spot data only (NIFTY/
              BANKNIFTY/SENSEX), run manually via `python -m data.pull_history`.
              expiry_calendar.py (done) resolves the REAL nearest listed
              option expiry from the live scrip master - verified live
              (2026-09-15) that NIFTY's actual expiry weekday is Tuesday
              (NSE changed this from Thursday in a 2025 rule change),
              SENSEX is Thursday, BANKNIFTY is monthly-only. LIVE-ONLY
              (no historical expiry archive exists) - deliberately NOT
              wired into backtesting/event_loop.py's shared process_bar()
              since NIFTY's own real expiry-day convention changed
              WITHIN the backtester's multi-year window, so no single
              fixed assumption is correct there; wiring this into live
              paper trading is separate future work of its own.
              lot_size.py (done) - same live-verified-not-hardcoded
              approach for lot size (NIFTY=65, BANKNIFTY=30, SENSEX=20
              as of 2026-09-15) - used by backtesting/cost_adjustment.py
features/     (Phase 4, done) multi-timeframe fusion (per-timeframe regime
              from market_state/, alignment/conflict scoring across them)
              (Phase 5, done) theoretical options/Greeks engine - Black-
              Scholes driven by real spot data + realized-vol proxy, NOT
              real market IV (SmartAPI has no historical option premium
              data - see features/theoretical_options.py for the full,
              permanent limitations of this). (Global/derivatives/news
              context, done) market_context.py (India VIX/PCR/OI buildup
              - SmartAPI, reuses the existing broker session, zero new
              credentials), global_context.py (S&P500/Dow/Nasdaq/Crude
              WTI/USD-INR via a keyless-but-unofficial Yahoo endpoint,
              plus World Bank macro indicators), news_sentiment.py
              (keyless RSS + VADER w/ a finance lexicon extension),
              economic_calendar.py (finnhub.io, optional free key -
              gracefully skips if unset). ALL of this is LIVE-ONLY and
              informational only - no historical series exists for any
              of it, so none of it is backtestable, and none of it is
              wired into any trading decision - same "collect and log
              first, gate later once proven" discipline `main`'s own
              premarket_bias.py established for this exact data.
              Adapted from `main`'s already-proven trading_bot/market_context.py
              + news_sentiment.py rather than re-derived from scratch.
              Market breadth (NIFTY50/BANKNIFTY constituent advance/
              decline) deliberately NOT built this pass - meaningfully
              more complex (per-symbol live quotes across dozens of
              constituents), left for a future increment
market_state/ (Phase 3, done) pre-indicator structure/volatility/
              momentum/liquidity classification (no RSI/MACD - see
              spec's own "don't start with indicators" principle)
strategies/   (Phase 6, done) strategy portfolio, regime-eligibility
              gating. 3 of ~10 spec-listed types built so far: trend-
              following, mean-reversion, opening-range-breakout. More
              (momentum, VWAP, failed-breakout, volatility-expansion,
              event-driven, structure-reversal) can extend this same
              framework later. (Phase 9, done) ranking.py picks among
              multiple candidate signals by confidence; contract_selection.py
              evaluates 5 strikes by capital efficiency (delta/premium -
              an honest heuristic, NOT real risk-adjusted EV, which
              needs a probability model that doesn't exist yet) instead
              of always trading ATM - opt-in in the backtester
              (BACKTESTS.md's Run 004), off by default.
              select_affordable_contract() (done) walks strikes outward
              until finding one that fits a real rupee risk budget -
              motivated directly by Run 007/008's capital-adequacy
              finding, see backtesting/ below
models/       (Phase 7, in progress) ML training. Model 1/8 (regime
              classifier) built - see models/EXPERIMENTS.md for its
              honest result (did not beat baseline at any horizon
              1-20 bars tried, not promoted). Model 2/8 (direction-
              probability, direction_classifier.py) built next -
              walk-forward validation applied from its first experiment
              (a lesson already learned from Model 1's own history).
              Also NOT promoted at any of 5 horizons (1/3/5/10/20 bars)
              tried: mean AUC never leaves a 0.499-0.565 band across all
              15 horizon-instrument combinations (essentially no better
              than random ranking), and a different single instrument
              marginally clears the 60% promotion threshold at each
              horizon with no consistent pattern - the signature of
              noise crossing an imperfect threshold, not a real signal.
              Two of the spec's 8 models now show the same honest
              conclusion: the current feature set (ATR/ROC/momentum
              only) has no exploitable signal for either target tried;
              a materially different feature set (MTF alignment or
              theoretical Greeks as inputs) is the next real attempt,
              not another target swept against the same five features.
              Tried theoretical Greeks next (Experiment 006):
              build_options_features() adds realized_vol/theoretical
              gamma/vega (option_type-symmetric, verified leak-safe) to
              Model 2's feature set. Result is genuinely mixed, not a
              clean resolution either way - NIFTY and SENSEX now pass
              the 60% promotion threshold TOGETHER at horizons 5 and 10
              (a more consistent pattern than the fully-scattered base
              result), but they're known-correlated broad indices (same
              "not independent confirmations" caveat BACKTESTS.md
              already applies elsewhere), and BANKNIFTY fails at every
              single horizon, mostly worse than its own base-feature
              result. Reported as a promising but unconfirmed lead, not
              a validated improvement - explaining BANKNIFTY's
              consistent divergence is the real next step before
              trusting the NIFTY/SENSEX pattern. Investigated that next
              step: found (via the live scrip master, not assumed) that
              Experiment 006 used the WRONG BANKNIFTY strike spacing
              (Rs.50 instead of the real Rs.100). Re-ran with the fix -
              result is nearly identical at every horizon, cleanly
              RULING OUT a wrong strike as the explanation (realized_vol
              doesn't depend on strike, and gamma/vega barely move over
              a Rs.50 gap on a ~Rs.55,000 underlying). The SAME bug
              mattered much more for BACKTESTS.md's Run 013 (equity-
              curve sequence risk), where it changed which trades were
              even affordable, and for Run 008 itself (re-verified as
              likely already correct - see that Run's own update).
              Checked a second hypothesis too: BANKNIFTY is real-world
              MONTHLY-only (NSE discontinued its weeklies, already
              live-verified), unlike NIFTY/SENSEX which still have real
              weeklies, yet every feature uses the same fixed 7-day
              synthetic expiry - re-ran with a realistic 28-day expiry
              for BANKNIFTY specifically, again nearly identical results,
              also ruled out. Stopped there deliberately: continuing to
              adjust parameters until BANKNIFTY's numbers converge with
              NIFTY/SENSEX's would itself resemble the p-hacking this
              project's discipline exists to prevent. Two independent,
              well-motivated technical hypotheses ruled out is now
              treated as real evidence the divergence is a genuine
              structural difference (sectoral vs. broad-market index),
              not an unsolved bug.
              models/feature_engineering.py provides vectorized, causal,
              whole-series equivalents of market_state/'s per-point
              classifiers, needed to make training-set construction
              tractable. Meta-labeling/calibration (Phase 8) not started
risk/         (done, ahead of the ML phases since it's rule-based and
              safety-critical) daily P&L selectivity engine, equity-
              protection capital tiers, risk-based position sizing,
              dynamic ATR/structure-based stop-loss/target/trailing
              (EV-based targets and ML-probability trailing explicitly
              deferred - they need Models 2/6, not built yet). Portfolio-
              level Greek aggregation now in portfolio/, see below
execution/    (done, always simulated - see below) order placement +
              realistic fill confirmation (polls the broker's order
              book for a terminal status, never assumes LTP=fill or
              that placing an order means it filled). place_order() is
              gated in the client itself: only calls the real broker if
              environment=="live" AND dry_run=False BOTH agree - codex
              is nowhere near a live go-live gate, so this always
              simulates today. transaction_costs.py (done) - centralized
              real Indian F&O round-trip cost model (brokerage/STT/
              exchange charges/SEBI fee/stamp duty/GST), adapted from
              `main`'s own validated trading_bot/costs.py
portfolio/    (done) aggregated delta/gamma/vega/theta across NIFTY/
              BANKNIFTY/SENSEX positions, with a concentration check so
              a single instrument can't quietly dominate total exposure
              even while every individual position looks fine. The
              scenario this module was built for - all three instruments
              genuinely sharing one capital pool - is now actually
              exercised in a real backtest by
              backtesting/portfolio_equity_simulation.py (Run 014, see
              backtesting/ below), not just standalone infrastructure
backtesting/  (Phase 12, done) event-driven backtester - processes bars
              strictly in order, leakage-free (see event_loop.py's
              docstring for its remaining first-pass simplifications:
              one conceptual unit not lot-sized, fixed 7-day expiry, no
              costs/slippage - the "daily bars only" simplification is
              resolved as of Run 011, see below: process_bar() now takes
              a real calendar-day-aware is_new_day flag instead of
              assuming every call is a new day). The stop-vs-target
              same-bar tie-break assumption (Investigation 003,
              tie_break_validation.py) is also resolved, and unusually
              cleanly: checked across the FULL 5-year history on all
              three indices (not just a recent window) and found the
              rule was NEVER actually invoked - zero trades in 286
              closed across this whole log ever closed on a bar
              breaching both stop and target simultaneously, given
              risk/dynamic_stops.py's current ATR-based widths. Every
              result in this log is unaffected by this assumption, not
              because it was proven right, but because it never
              mattered. (Phase 13, done) walk-forward
              validation (walk_forward.py) - non-overlapping, embargoed
              folds, each an independent backtest. monte_carlo.py adds
              bootstrap total-P&L confidence intervals and trade-
              sequence-reshuffling drawdown analysis. cost_adjustment.py
              (done) closes the "no costs" gap as a post-hoc analysis
              (scales to a real, live-verified lot size via
              data/lot_size.py, applies execution/transaction_costs.py) -
              Run 005 found costs are real but a minor drag (~1.4-1.8%
              of gross P&L) in this backtest's parameter regime, no
              trades or folds flip sign. slippage_sensitivity.py (done)
              sweeps assumed slippage (0-5%, since no real historical
              spread data exists to derive one number from - a
              deliberate sweep, not a fabricated point estimate) - Run
              006 found the P&L conclusion robust across the whole
              range, zero fold flips. equity_simulation.py (done) - a
              REAL account-equity curve using actual risk-based position
              sizing + equity-protection tiers + real transaction costs
              (not the illustrative fixed-lot-size view the other
              modules use). Run 007's finding is the single most
              important result in this whole log: at the spec's own
              Rs.50,000 starting capital, a CONSERVATIVE 1-5% risk-per-
              trade affords ZERO trades on ANY of the three indices -
              real 2026 index-option premiums x real lot sizes cost a
              double-digit percentage of that capital per lot. Higher
              risk levels that DO size show extreme, path-dependent
              ruin-or-blowup outcomes (not a trading signal - a symptom
              of undercapitalization). Directly motivates future work:
              either revisit the capital assumption, build affordability-
              aware strike selection, or move to defined-risk spreads
              instead of naked long options. strategies/contract_selection.py's
              new select_affordable_contract() (done) + a matching
              simulate_equity_curve_with_affordable_contracts() directly
              follow up on this (Run 008): affordability-aware strike
              selection DOES let Rs.50,000 participate at a conservative
              1% risk-per-trade (94/94, 94/97, 91/92 trades sized vs 0
              before) - but the exciting-looking headline compounded
              returns (+583% to +2144%) are NOT to be trusted, they're
              inflated by continuous 5-year compounding with no capital
              ever withdrawn. The honest, walk-forward (independent-
              fold) view is mixed - 4/5, 2/5, 3/5 folds profitable,
              similar inconclusive character to every other result here -
              and two real risks were flagged. tick_slippage.py (done) +
              a new tick_spread parameter on the equity simulation close
              the FIRST one (Run 009): premium-scale-aware, tick-based
              slippage (verified real tick size Rs.0.05, NOT the raw
              scrip-master field's misleading "5.000000" - that schema
              scales price fields by 100, confirmed via the strike
              field) leaves the fold-level picture essentially unchanged
              up to Rs.5/unit round-trip, with a real sharp breakdown
              threshold only far out (~Rs.20/unit). moneyness_analysis.py
              (done) closes the SECOND risk (Run 010): scans each trade's
              full hold-period high/low (not just entry/exit) for whether
              spot ever crossed the affordability-selected far-OTM
              strike. "Target" exits are real, strike-crossing convexity
              events (74-100% crossed across NIFTY/BANKNIFTY/SENSEX, mean
              closest approach -1.4% to -2.0% ITM) - the far-OTM
              exit-timing-reuse concern does not materialize. "Stop"
              exits are mostly NOT strike-approach events (only 20-37%
              crossed) - a legitimate but previously undocumented
              distinction: most stops are premium-decay exits, not
              wrong-way-move exits, for far-OTM contracts specifically.
              Run 011 closes the "daily bars only" gap disclosed since
              Run 001: event_loop.py's process_bar() unconditionally
              reset the daily-loss kill switch on every call, correct
              only because every prior run used daily bars - fixed via
              a real is_new_day flag computed from actual calendar-day
              boundaries (new _bar_date() helper), not bar count; 4 new
              tests, full existing suite unchanged. First-ever intraday
              (5-minute bar) backtest on real data (~4 months,
              NIFTY/BANKNIFTY/SENSEX) is the cleanest positive result in
              this whole log - 12/12 walk-forward folds profitable,
              bootstrap 90% CIs excluding zero on all three, survives
              realistic transaction costs (8-11% of gross, vs Run 005's
              1.4-1.8% at daily trade frequency) - but explicitly NOT
              claimed as a validated edge: only ~4 months of data (vs 5
              years elsewhere in this log) and every ATR/lookback
              parameter was tuned against daily-bar semantics, now
              running ~100x faster than validated for - a promising
              first look, not yet on the same footing as the daily-bar
              findings. Run 012 followed up with LONGER history rather
              than intraday parameter tuning - tuning against the same
              window Run 011 already showed positive would be in-sample
              overfitting with no held-out data to check it against, so
              deliberately not done. Instead reused already-pulled
              hourly data (data/raw/*/ONE_HOUR.parquet, 498 real days,
              2024-2026, no new pull needed, zero code changes since the
              day-boundary fix already generalizes) - result is the
              most robust intraday evidence in this log: 15/15
              walk-forward folds profitable across all three indices,
              bootstrap CIs excluding zero, costs an even smaller drag
              (1.9-2.5% of gross) than either the daily or 5-minute
              runs. Two different intraday granularities now agree, but
              still not claimed as a validated edge - spot-proxy pricing
              and a look-ahead-overlap caveat (the window overlaps
              period the daily strategies were themselves built against)
              remain open; genuinely fresh out-of-sample intraday data
              is the honest next step, not further tuning against what's
              already been seen. Investigation 002 started sourcing that:
              no new infrastructure needed (data.pull_history already
              backfills FIVE_MINUTE/ONE_HOUR daily on the shared EC2 box
              via deploy/codex-paper-trading.timer, and save_ohlcv
              already dedups-and-merges by timestamp) - confirmed by
              running it locally, zero errors across all three indices.
              A FRESH_OOS_START_DATE marker (2026-09-10 15:30 IST, Run
              011/012's snapshot end) is now fixed so a future
              re-analysis knows exactly which bars are genuinely
              untouched. Honestly, only 2 fresh trading days exist so far
              (nowhere near Run 011's smallest fold of ~21 days) - no
              result reported, deliberately, rather than compute one from
              2 days; revisit after ~20+ fresh days accumulate. Run 013
              (equity_sequence_risk.py) closes a gap between Run 003's
              raw-P&L reshuffle test and Run 008's real compounding
              curve: reshuffling the ORDER of Run 008's own trades 5,000
              times (values held fixed, only the sequence varies) shows
              86-100% of reshuffles (per instrument) did WORSE than the
              real historical order - the reshuffled mean (NIFTY/SENSEX
              +60-64%, BANKNIFTY +296% after a strike-spacing bug fix -
              see Run 013's own correction note) is well below Run 008's
              headline (+583-882%), which sits toward the best-case tail
              of possible orderings. Quantifies, for the first time, what
              Run 008 could only assert: its headline number is an
              artifact of favorable sequencing, not a representative
              outcome - the reshuffled mean is the honest number. Run
              014 (portfolio_equity_simulation.py) closes the biggest
              remaining structural gap: every prior Run treats
              NIFTY/BANKNIFTY/SENSEX as three INDEPENDENT Rs.50,000
              accounts, never one real account where all three compete
              for the same capital. Reuses each instrument's own
              entry/exit timing unchanged, adds a genuinely new
              mechanic (tracking cash actually locked up in open
              positions, not just capital settled at exit) - verified
              exactly correct in the degenerate single-instrument case
              (bit-for-bit match with the existing simulation). Real
              3-way concurrency occurs at least once across the 5-year
              history, and the pooled account reaches ~97% of the
              ABSOLUTE ending capital three separate pools would reach
              combined, using only 1/3 the total capital (Rs.50,000 vs
              Rs.150,000) - a genuine capital-efficiency finding.
              Explicitly NOT claiming the pooled headline return itself
              is validated: it carries the same sequence-risk caveat
              Run 013 already proved dominates these compounding
              curves, likely amplified by pooling three instruments'
              trades into one shared trajectory - sequence-risk testing
              the portfolio curve is the flagged, not-yet-done next step.
              BACKTESTS.md logs every run honestly, including a real bug
              it caught (daily risk state never resetting between bars)
              and the corrected, still-mixed result after fixing it -
              nothing here is a validated edge
research/     (Phase 17/18, structural backbone only) experiment_log.py -
              a durable, queryable JSONL ledger of every experiment run
              (research/experiment_log.jsonl, backfilled with tonight's
              real results from models/EXPERIMENTS.md and
              backtesting/BACKTESTS.md), plus has_been_tried() to avoid
              blindly repeating an already-tried hypothesis.
              promotion_gate.py encodes "promote only if justified
              against a real baseline" as actual code (default: beat
              baseline in >=60% of >=5 folds), not just written
              discipline. NOT a claim of real autonomy - hypothesis
              generation is still done by a human/Claude reading the
              data, not by this code
paper_trading/ (Phase 14, code done - not yet scheduled to run) once-
              daily loop reusing backtesting/event_loop.py's exact
              process_bar() (never a divergent live reimplementation),
              persisted per-instrument state, Telegram entry/exit
              notifications. Never imports execution/order_client.py -
              structurally cannot place a real order
dashboard/    (Phase 15, done) Streamlit - Data Health, current Market
              State + regime timeline, an on-demand Backtest viewer, and
              a Paper Trading tab (reads the SAME persisted state
              paper_trading/daily_loop.py writes - sync
              .state/paper_trading/ down from the instance to review its
              live history locally, same pattern as data/raw/). Read-
              only, informational only. Run: `streamlit run
              dashboard/app.py`. dashboard/queries.py + backtest_data.py
              + paper_trading_queries.py hold the testable logic; app.py
              is a thin rendering layer. A real bug was caught only by
              actually running this in a browser (not just importing it
              in tests): a same-named dashboard/data.py module collided
              with the top-level data/ package once Streamlit put
              dashboard/ on sys.path - renamed to queries.py to fix it
              properly
tests/        test-first, mirrors every module above
deploy/       systemd unit, CI (deploys to the shared instance via SSM)
```

## Local development

```
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
python -m app.main   # starts the Phase 1 health-check skeleton
python -m data.pull_history   # backfill real historical data into data/raw/ (gitignored)

# Dashboard - separate requirements file (streamlit is heavy and never
# needed by the live service - see requirements-dashboard.txt)
.venv/Scripts/pip install -r requirements-dashboard.txt
streamlit run dashboard/app.py
```

Required environment variables are validated at startup by
`config/settings.py` - see `.env.example`.
