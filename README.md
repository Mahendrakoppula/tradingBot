# Codex — Autonomous ML-Driven Index Options Trading System

Status: **Phase 1 of 18** (project scaffolding, config, logging,
notifications, and the AWS/Docker infrastructure to run it). No trading
logic exists yet.

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
data/         (Phase 2+) ingestion, storage, quality checks
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
deploy/       Dockerfile, docker-compose.yml, systemd unit, CI
```

## Local development

```
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt -r requirements-dev.txt
docker compose -f deploy/docker-compose.yml up -d timescaledb
python -m pytest -q
python -m app.main   # starts the Phase 1 health-check skeleton
```

Required environment variables are validated at startup by
`config/settings.py` - see `.env.example`.
