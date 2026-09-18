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

**2026-09-17: M1 merged to main and deployed** (SHADOW unit live on the box, memory
journal until the DEPLOY.md §8 infra - t3.small resize, PostgreSQL 16,
`TECH_DATABASE_URL` in SSM - is done; that plus one Postgres-backed session is the
M1 gate).

**2026-09-18: M2 fully built** on 9 stacked branches (`feature/m2-strategies` ->
`m2-scoring` -> `m2-expected-move` -> `m2-options` -> `m2-stops` -> `m2-risk` ->
`m2-execution` -> `m2-positions` -> `m2-paper-loop`). SHADOW now runs the whole
§93 pipeline and journals every decision; `TECH_MODE=PAPER` fills approved
signals in the in-process paper broker. Finding for M3: at Rs.50k x 0.5% one
NIFTY lot fits ~3.3 pts of ALL-IN option risk, so most structural stops are
rejected at 0.5% (and many at 1%) - the risk engine is doing its job; the
capital/lot mismatch is a real constraint the paper phase must quantify.

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
| 10 | Strategies (10 families) | [x] | M2 | `engine/strategies/`: all ten §14 families v0.1, regime/alignment routing, counter-trend evidence bar, family hints. |
| 11 | Signal Scoring / Ranking | [x] | M2 | `engine/scoring.py`: 9 components + 12 penalties, rank() with correlated-bucket exclusion. |
| 12 | Option Selection | [x] | M2 | `engine/option_chain.py` cache (FULL quotes + broker Greeks, BS model for BFO), `engine/option_select.py` multi-contract score, `engine/bs.py`. |
| 13 | Expected Move / No-Chase / Decay | [x] | M2 | `engine/expected_move.py` (estimate, no_chase, distance_check, non-linear option response), `option_select.decay_filter`. |
| 14 | Dynamic SL / Target / Trailing | [x] | M2 | `engine/stops.py`: structural SL -> option scale, TP1/TP2/extended, trailing that only tightens, ThesisMonitor with the 15 exit reasons. |
| 15 | Risk / Position Sizing / Costs | [x] | M2 | `engine/risk_engine.py`: §29 priority order, all-in per-unit sizing, EV, tiers A/B/C, account limits, CostBreakdown. |
| 16 | Execution Engine | [x] paper | M2 | `engine/execution/`: snapshot lock + drift, gate, 12-state order machine, timeouts/partials/duplicates/latency, PaperBroker (3 profiles). Live adapter is M4. |
| 17 | Portfolio / Reconciliation | [x] | M2 | `engine/positions.py`: PositionBook, reconcile() vs broker, KillSwitches + circuit breaker, EOD sweep. |
| 18 | Backtest | [~] | M3 | `framework/backtest_engine.py` (bar-by-bar, spot-proxy for options), `backtest_technical.py` resampler. **Missing**: event-driven multi-TF replay through the SAME engine used live (§51 parity), option-premium proxy honesty. |
| 19 | Journal / Dataset | [x] | M2 | All schema-v1 tables populated: signals (status), risk_decisions, executions, trade_results, kill_switch_events, option_chain_snapshots; `engine/rejections.py` §72 dataset. |
| 20 | EOD Analysis | [~] | M2 | `engine/eod.py`: signals by status, trades/wins/gross/costs/net, exit reasons, rejections by stage. Per-fingerprint/strategy breakdowns and the nightly review are M3. |
| 21 | Walk-Forward | [~] | M3 | `framework/walk_forward.py` (chronological, no embargo). |
| 22 | Monte Carlo | [~] | M3 | `framework/monte_carlo.py` (bootstrap P&L, ruin). |
| 23 | Paper Trading | [x] | M2 | `engine/paper_loop.py` PaperLoop: SHADOW (journal) and PAPER (paper broker) on the same pipeline. |
| 24 | Shadow Strategies | [ ] | M3 | Run candidate strategies in SHADOW alongside PAPER, compare (§81). |
| 25 | Controlled Live Deployment | [ ] | M4 | Gated on §79 gates 1–4; start at ₹60–75 risk (§80). |
| 26 | Live Observability | [~] | M4 | Telegram notifiers, systemd/journalctl, S3 sync. **Missing**: health monitoring (§87), latency/slippage dashboards. |
| 27 | Continuous Research | [~] | M4 | Nightly agent + framework runners. |
| 28 | Future AI/ML/NLP | [ ] | — | Explicitly out of V1 (§82). |

## M2 build order — PAPER (one PR each, every one green on CI)

Same engine in SHADOW/PAPER/LIVE (§51). M2 adds the decision pipeline after
TRADE_READY (§93 STRATEGY ROUTING … EXIT) and a paper broker. **Nothing in M2
can reach a real order endpoint**: `PaperBroker` is the only broker, the AST
guard stays on `engine/`, and `LiveBroker` (M4) is the single allow-listed
exception when it lands.

| Step | Branch | Spec | Delivers |
|---|---|---|---|
| 1 | `feature/m2-strategies` | §14 §15 §63 | `engine/strategies/`: 10 families as versioned `Candidate` / `NoTrade` evaluators with objective entry/confirmation/invalidation/target refs on the UNDERLYING; regime+alignment routing table; fingerprint builder; extra context fields (recent sweeps/regimes, VWAP cross). |
| 2 | `feature/m2-scoring` | §16 §17 §35 | `engine/scoring.py` 9-component 0–100 + penalties; opportunity ranking, correlated-index exclusion. |
| 3 | `feature/m2-expected-move` | §21 §22 §23 §37 | `engine/expected_move.py`: remaining move (ATR × time-of-day × regime), no-chase, blocked-target check, non-linear option response (delta/gamma). |
| 4 | `feature/m2-options` | §19 §20 §24 | `engine/option_chain.py` candidate cache (chain + FULL quotes + Greeks, Black-Scholes IV/Greeks fallback for SENSEX), `engine/option_select.py` multi-contract scoring, decay filter. Reject setup if no acceptable option. |
| 5 | `feature/m2-stops` | §26 §28 §31 §32 | `engine/stops.py`: structural SL (underlying → option via delta), market-driven targets, trailing (structure/ATR/VWAP/EMA), thesis monitor, 15 exit reasons. |
| 6 | `feature/m2-risk` | §25 §27 §29 §30 §33 §34 | `engine/risk_engine.py`: all-in cost (CostRates + spread + slippage), sizing after SL with lot rounding, EV, tiers A/B/C with the ₹800 preference, account/daily/weekly/streak/heat limits → APPROVED/REJECTED/REDUCE_SIZE/WAIT_*; RiskSchema rows. |
| 7 | `feature/m2-execution` | §39–§46 §52 §47 | `engine/execution/`: snapshot lock, entry drift, execution gate, order state machine + timeouts + partial fills + duplicate guard + latency; `PaperBroker` realistic/conservative/ideal; broker interface. |
| 8 | `feature/m2-positions` | §54–§57 §69 §62 §70 §72 | positions + thesis-monitor loop, reconciliation vs the (paper) broker, kill switches + circuit breaker, EOD exit sweep, trade results/journal, rejected-signal dataset. |
| 9 | `feature/m2-paper-loop` | §38 §51 §53 §60–§65 | loop wiring: TRADE_READY → pipeline → paper order → management; `TECH_MODE=PAPER`; schema v2; config + bounds; EOD summary with P&L/costs; Telegram. |

M2 gate: a full paper session with realistic fills, every signal (taken or
rejected) journaled with its 20-key explanation and risk decision, EOD report
with gross/costs/net, reconciliation clean, zero real orders.

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
