# Roadmap — index-options engine (spec §91 phase checklist)

Tracking list for the second bot (`trading_bot.run_technical`, `trading_bot/engine/`),
built to the 94-section "Automated Indian Index Options Buying System" spec.
Update this file in the same PR that moves a phase.

Legend: `[x]` built + unit-tested (review happens at the milestone gate) ·
`[~]` partially exists (reuse, needs extension) · `[ ]` not started.

## Milestones (review gates — spec §74/§83/§91: never auto-activate the next phase)

| Milestone | Scope | Gate |
|---|---|---|
| **M1 — SHADOW** | phases 0–9 (+ journal half of 19): data foundation, all analysis engines, pre-signal, Postgres journal. **Zero orders.** | first live SHADOW session on the box: banner, warmup < 60 s, 5m bars match REST, `context_snapshots` every 5m per underlying, pre-signal events + would-be `signals` in Postgres, `run_daily.py` unaffected, AST no-orders test green |
| **M2 — PAPER** | phases 10–17, 19–20, 23: ten strategy families, scoring, option selection, expected move, SL/target, risk, execution state machine, reconciliation, paper simulator | realistic-fill paper trading with the full journal (§52) |
| **M3 — validation** | phases 18, 21–24: backtest parity through the same loop, walk-forward, Monte Carlo, shadow strategies, the four §79 paper gates | 4–12 weeks of paper (§78), gates 1–4 passed |
| **M4 — controlled live** | phases 25–27: one-lot cap, Rs.60–75 initial risk (§80), health monitoring (§87) | human decision; `TECH_MODE=LIVE` is a reviewed commit that also turns the nightly tuner off |

## Status

**2026-09-17: M1 fully built** (9 stacked branches, 508 tests). Not yet merged;
the M1 gate needs the off-hours infra in `deploy/DEPLOY.md` §8 (t3.small resize,
PostgreSQL 16, `TECH_DATABASE_URL` in SSM) and one live session.

M1 deviation, deliberate: every engine runs at the **5m close only** (no 1m-driven
CONFIRMING→TRADE_READY leg) — see `trading_bot/engine/shadow.py`. M2 revisits.

## Phase checklist

| # | Phase (spec §91) | Status | Milestone | What exists / notes |
|---|---|---|---|---|
| 0 | Architecture / Requirements | [x] | M1 | `engine/config.py` modes BACKTEST/RESEARCH/PAPER/SHADOW/LIVE + three-switch live gate; `engine/db/schema_v1.py`; deploy wiring. |
| 1 | Market Data / Angel One Foundation | [x] | M1 | `engine/feed.py` reconnecting WS supervisor over `ws_market.py`, `engine/quality.py` OK/STALE/GAP/INVALID/DISCONNECTED, `engine/ratelimit.py`. Live WS behaviour (esp. SENSEX on `bse_cm`) unverified until the first deploy. |
| 2 | Historical / Timeframe Data | [x] | M1 | `engine/warmup.py` REST warmup (400d/90d/21d/7d, chunked to API max-days) + today's 1m backfill; `engine/candles.py` tick→1m/5m/30m/1d; Postgres `candles` table. |
| 3 | Indicators | [x] | M1 | `indicators.py`: SMA/EMA/RSI/MACD/ATR/ADX/VWAP + ROC, Stochastic, Bollinger + width, OBV, percentile rank, relative volume, ATR-normalised slope, session VWAP. |
| 4 | Market Structure | [x] | M1 | `engine/structure.py`: lookahead-safe swings, HH/HL/LH/LL, BOS/CHoCH, MSS, PDH/PDL/PDC/PWH/PWL, session high/low + opening range, gaps, failed breakouts, liquidity sweeps, distance-to-level report. |
| 5 | Market Trend | [x] | M1 | `engine/trend.py`: per-TF score from structure/EMA order/slope/momentum/DI/VWAP → 10 labels incl. COUNTER_TREND/UNSTABLE/TRANSITION, persistence/acceleration/exhaustion; §8 weighted alignment → 6 labels. |
| 6 | Levels / Liquidity | [x] | M1 | Spot-side levels + `nearest_levels` + per-day touch counts in `engine/analysis.py`. Option-side liquidity (depth/spread/OI gates) is M2 (`liquidity.check_liquidity` exists). |
| 7 | Price Action | [x] | M1 | `engine/price_action.py`: candle anatomy (body/wick ratios, close location, displacement), engulfing/pin/inside/doji/morning-evening star/rejection. |
| 8 | Regime | [x] | M1 | `engine/regime.py`: 17-label taxonomy on three axes with priority order and 2-bar hysteresis (NO_TRADE/UNSTABLE immediate), transition strings. Regime-specific expectancy tracking is M3. |
| 9 | Pre-Signal | [x] | M1 | `engine/presignal.py`: NO_SETUP→EARLY_DEVELOPMENT→PRE_SIGNAL→CONFIRMING→TRADE_READY + EXTENDED/EXHAUSTED/EXPIRED/REJECTED, 8 evidence keys, decay + TTL, counter-trend evidence bar, deterministic ids. Emits data only — no order path (AST-tested). |
| 10 | Strategies | [~] | M2 | `framework/strategies.py`: 4 of 10 (breakout≈Compression→Breakout, trend_following≈Pullback, momentum≈Momentum Expansion, mean_reversion≈Range Extreme). ORB/Momentum trackers in `scalp_strategy.py`. **Missing**: Liquidity Sweep+BOS, MTF Confluence, VWAP Reclaim, PDH/PDL Trap, EMA20/50 Pullback; retest legs; regime/trend routing (§15). |
| 11 | Signal Scoring / Ranking | [~] | M2 | `framework/scoring.py` 6-component `ScoreWeights`/`score()`. **Missing**: spec's 9-component 0–100 table, penalties, opportunity ranking, correlation-aware selection. |
| 12 | Option Selection | [~] | M2 | `OptionChain`, `build_long_leg` (spot±OTM%), `get_option_greeks` endpoint (IV only parsed). **Missing**: multi-contract evaluation by delta/theta/IV/spread/OI, candidate cache (§20), "reject setup if no acceptable option". |
| 13 | Expected Move / No-Chase / Decay | [ ] | M2 | Nothing exists (§21–24). |
| 14 | Dynamic SL / Target / Trailing | [~] | M2 | `framework/risk.py`: `atr_stop_target`, `structure_stop_target`, `chandelier_stop`, `r_multiple_price`. **Missing**: thesis-invalidation exits, VWAP/structure trailing, the 14 exit-reason taxonomy (§32). |
| 15 | Risk / Position Sizing / Costs | [~] | M2 | `risk.risk_based_quantity`, `evaluate_portfolio_risk`, `correlated_exposure_multiplier`; `costs.CostRates` (all-in costs). **Missing**: 0.25–1.0% hard per-trade risk with all-in cost in the risk figure, weekly loss, consecutive-loss, portfolio heat, ₹800 preference tiering (§29), EV (§30). |
| 16 | Execution Engine | [~] | M2 | `LongOptionStrategy.enter/exit`, `place_split_order` (freeze-qty), `entry/exit_limit_price`, paper hard-gate. **Missing**: execution gate (§41), order state machine (§42), timeouts (§43), partial fills (§44), duplicate protection (§45), latency measurement (§46), signal snapshot lock + entry drift (§39–40), realistic paper simulator (§52). |
| 17 | Portfolio / Reconciliation | [ ] | M2 | `get_order_book`/`get_trade_book` exist; **no** `get_positions`, no order-details lookup, no reconciliation loop (§54–55), no kill switches beyond daily-loss (§56). |
| 18 | Backtest | [~] | M3 | `framework/backtest_engine.py` (bar-by-bar, spot-proxy for options), `backtest_technical.py` resampler. **Missing**: event-driven multi-TF replay through the SAME engine used live (§51 parity), option-premium proxy honesty. |
| 19 | Journal / Dataset | [x] M1 part | M1→M2 | `engine/db/`: runs, config_versions, candles, context_snapshots, presignal_events, signals populated in M1; strategy_versions, option_chain_snapshots, risk_decisions, executions, trade_results, kill_switch_events DDL-only until M2. Rejected-signal dataset (§72) and fingerprints (§63) M2. |
| 20 | EOD Analysis | [~] | M2 | `engine/eod.py` M1 summary (snapshots/stage counts/would-be signals by underlying/direction/regime). Nightly review agent exists (`deploy/daily_review_prompt.md`). Missing: automated EOD exit sweep (§69), per-strategy/fingerprint breakdowns, rejected-signal analysis. |
| 21 | Walk-Forward | [~] | M3 | `framework/walk_forward.py` (chronological, no embargo). |
| 22 | Monte Carlo | [~] | M3 | `framework/monte_carlo.py` (bootstrap P&L, ruin). |
| 23 | Paper Trading | [~] | M2–M3 | `DRY_RUN` gating exists; needs realistic/conservative/ideal fill models (§52) and the 4 gates (§79). |
| 24 | Shadow Strategies | [ ] | M3 | Run candidate strategies in SHADOW alongside PAPER, compare (§81). |
| 25 | Controlled Live Deployment | [ ] | M4 | Gated on §79 gates 1–4; start at ₹60–75 risk (§80). |
| 26 | Live Observability | [~] | M4 | Telegram notifiers, systemd/journalctl, S3 sync. **Missing**: health monitoring (§87), latency/slippage dashboards. |
| 27 | Continuous Research | [~] | M4 | Nightly agent + framework runners. |
| 28 | Future AI/ML/NLP | [ ] | — | Explicitly out of V1 (§82). |

## Where things live

| Area | Module |
|---|---|
| Config / modes / live-order gate | `engine/config.py` (`EngineConfig`, `broker_config`) |
| Session clock (09:15-anchored bars) | `engine/clock.py` |
| Ticks → candles, stores | `engine/candles.py` |
| WebSocket feed (reconnect, health) | `engine/feed.py` |
| REST warmup + backfill, rate limiting | `engine/warmup.py`, `engine/ratelimit.py` |
| Data quality gate | `engine/quality.py` |
| Indicators / price action / structure | `indicators.py`, `engine/price_action.py`, `engine/structure.py` |
| Trend (10 labels) + alignment (6) | `engine/trend.py` |
| Regime (17 labels, hysteresis) | `engine/regime.py` |
| Context snapshot, full recompute | `engine/context.py`, `engine/analysis.py` |
| Pre-signal state machine | `engine/presignal.py` |
| Explainability (20 keys), JSON logs | `engine/explain.py`, `engine/jsonlog.py` |
| Postgres schema / DAL / memory twin | `engine/db/` |
| Loop, replay, EOD, instruments | `engine/shadow.py`, `engine/replay.py`, `engine/eod.py`, `engine/instruments.py` |
| Entry point | `run_technical.py` |
| Safety guard | `tests/test_engine_no_orders.py`, `tests/test_config_bounds.py` |
