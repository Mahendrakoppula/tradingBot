# Codex — Autonomous ML-Driven Index Options Trading System

Status: **Phase 2 of 18** (Phase 1: project scaffolding, config, logging,
notifications, deploy infra - done. Phase 2: historical OHLCV data
pipeline + quality engine for the three in-scope indices - done). No
trading logic exists yet.

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
features/     (Phase 4-5) pre-indicator market-state + multi-timeframe features
market_state/ (Phase 4) regime/structure classification
strategies/   (Phase 7) strategy portfolio, eligibility-by-regime
models/       (Phase 8-9) ML training, meta-labeling, calibration
risk/         (Phase 11) position sizing, portfolio risk, hard limits
execution/    (Phase 17) order execution, reconciliation
portfolio/    (Phase 11) aggregated Greeks/exposure across instruments
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
