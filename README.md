# Codex — Autonomous ML-Driven Index Options Trading System

Status: **Phase 9, 14, and 15 done, Phase 17/18 structural backbone done, global/derivatives/news context engines done, Phase 7 in progress** (of 18). **IMPORTANT: Run 007 (backtesting/BACKTESTS.md) found the spec's own Rs.50,000 capital cannot afford even one lot of NIFTY/BANKNIFTY/SENSEX options at ATM at a conservative risk-per-trade percentage, given real current lot sizes/premiums. Run 008's affordability-aware strike selection is a genuine partial fix (Rs.50,000 CAN now participate, far-OTM), but its own headline returns are explicitly flagged as untrustworthy (inflated by continuous compounding) - read both entries before assuming this capital figure is workable as stated.** (Phase 1: scaffolding/config/
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
              honest result (did not beat baseline, not promoted).
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
              even while every individual position looks fine
backtesting/  (Phase 12, done) event-driven backtester - processes bars
              strictly in order, leakage-free (see event_loop.py's
              docstring for its known first-pass simplifications: daily
              bars only, one conceptual unit not lot-sized, fixed 7-day
              expiry, no costs/slippage). (Phase 13, done) walk-forward
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
              threshold only far out (~Rs.20/unit). The SECOND risk
              (validating entry-timing-reuse for far-OTM convexity, not
              just Run 004's near-ATM finding) remains open.
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
