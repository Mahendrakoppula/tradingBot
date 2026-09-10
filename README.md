# Codex — Autonomous ML-Driven Index Options Trading System

Status: **Phase 7 of 18, in progress** (Phase 1: scaffolding/config/
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
evaluated against real data; it did NOT beat the naive baseline - see
models/EXPERIMENTS.md for the honest result and why it wasn't tuned to
look better). No live trading logic exists yet - nothing here is
promoted or wired into a live decision.

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
              BANKNIFTY/SENSEX), run manually via `python -m data.pull_history`
features/     (Phase 4, done) multi-timeframe fusion (per-timeframe regime
              from market_state/, alignment/conflict scoring across them)
              (Phase 5, done) theoretical options/Greeks engine - Black-
              Scholes driven by real spot data + realized-vol proxy, NOT
              real market IV (SmartAPI has no historical option premium
              data - see features/theoretical_options.py for the full,
              permanent limitations of this)
market_state/ (Phase 3, done) pre-indicator structure/volatility/
              momentum/liquidity classification (no RSI/MACD - see
              spec's own "don't start with indicators" principle)
strategies/   (Phase 6, done) strategy portfolio, regime-eligibility
              gating. 3 of ~10 spec-listed types built so far: trend-
              following, mean-reversion, opening-range-breakout. More
              (momentum, VWAP, failed-breakout, volatility-expansion,
              event-driven, structure-reversal) can extend this same
              framework later
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
execution/    (Phase 17) order execution, reconciliation
portfolio/    (done) aggregated delta/gamma/vega/theta across NIFTY/
              BANKNIFTY/SENSEX positions, with a concentration check so
              a single instrument can't quietly dominate total exposure
              even while every individual position looks fine
backtesting/  (Phase 12-13) event-driven backtester, walk-forward/OOS
research/     (Phase 18) autonomous hypothesis generation/validation loop
dashboard/    (Phase 15) Streamlit
tests/        test-first, mirrors every module above
deploy/       systemd unit, CI (deploys to the shared instance via SSM)
```

## Local development

```
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
python -m app.main   # starts the Phase 1 health-check skeleton
```

Required environment variables are validated at startup by
`config/settings.py` - see `.env.example`.
