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

## Status (as of 2026-09-18, end of day)

**Built: 26 of 29 phases. Validated: none yet.** Everything through M4a is merged
to `main` and running on the instance in SHADOW mode with a PostgreSQL journal.
The two phases not built are deliberate: 25 (live deployment) waits for the §79
gates to pass on real paper data plus a §83 promotion record per strategy; 28
(AI/ML) is outside V1 by the spec's own rule (§92 #51).

"Built" is not "validated". Phases 18-24 exist as code, but their gates are
empirical - weeks of paper sessions, 100+ valid opportunities, walk-forward
stability, Monte Carlo ruin probability, execution parity - and no paper session
has run yet. From here the work is running, reading the nightly review, and
making two decisions with data: the capital / risk-per-trade question, then
`TECH_MODE=PAPER`.

| Milestone | State | Evidence |
|---|---|---|
| M1 SHADOW | merged 2026-09-17, deployed, **gate met** 2026-09-18 | Postgres-backed session: 93 snapshots, 84 pre-signal events, warmup 18-90 s, daily bot unaffected, AST no-orders test green |
| M2 PAPER (code) | merged + deployed 2026-09-18 | full §93 pipeline runs live on every TRADE_READY; paper broker exists; `TECH_MODE` still SHADOW |
| M3 validation (tooling) | merged 2026-09-18 | `research_cli` metrics/review/gates/walkforward/montecarlo/backtest; **gate not attempted** (no paper data) |
| M4a observability | merged + deployed 2026-09-18 | health alerts to Telegram, JSON heartbeat, restart recovery, review timer 15:45 IST |
| M4b live adapter | **not built** | blocked on M3 gate (§74 "never skip validation") |

**Infrastructure done 2026-09-18:** PostgreSQL 16.15 on the instance, peer auth
over the Unix socket (no password, no SSM parameter), 1 GB swap, sized for the
t3.micro; nightly pg_dump into `.state/pgdump/` (S3 via the existing sync
timer). The t3.small resize is optional and needs credentials the deployer
lacks (DEPLOY.md §8).

**First shadow-session finding (2026-09-18):** all nine TRADE_READY setups
were rejected at STRATEGY_ROUTING because `is_counter_trend()` treated the
alignment label COUNTER_TREND (raised on any timeframe conflict, even the 1m)
as counter-trend for both directions. Fixed the same evening (direction-aware,
§7). Monday 2026-09-21 is the first session that can show real pipeline
throughput.

**Open questions the paper phase must answer before M4b:**
1. At Rs.50k x 0.5% one NIFTY lot fits ~3.3 pts of all-in option risk; most
   structural stops need more. Expect `RISK_ENGINE: one_lot_exceeds_max_risk`
   to dominate; decide capital vs risk-per-trade (both within §25's ranges).
2. How often COUNTER_TREND / regime gating still blocks setups after the fix.
3. Whether SENSEX (model Greeks, wider spreads) is worth keeping in the set.

**Candidate refinements (from paper-session observations - not applied; §71
"never change the strategy because of one trade or one day". Each needs to recur in
the nightly reviews before it becomes a versioned change with a promotion record.)**

| # | Observed | Candidate change | Evidence so far |
|---|---|---|---|
| R1 | 2026-09-21 NIFTY 09:40-10:10: double top at PDH 23389 (two bearish pins, `repeated_tests`), pre-signal reached TRADE_READY on the neckline close at 10:10, but `PDH_PDL_TRAP` returned `trap_without_confirmation` because it also demands a bearish candle pattern or swing BOS on the trigger bar. The tracker's own `confirmation_closed` (close back through the level after the sweep) is the classic trap confirmation. Counterfactual: the short ran 16 pts then a 100-pt rally - shallow reversal in a STRONG_BULL 5m regime. | Let `PDH_PDL_TRAP` (and `LIQUIDITY_SWEEP_BOS`) accept "close back through the swept level after >=2 tests" as confirmation, optionally with a relative-volume condition, as strategy v0.2 behind a promotion record. | 1 occurrence |
| R2 | 2026-09-18/21: with the daily strongly bearish and the 30m rallying, every setup is counter-trend one way or the other, so only the reversal families can trade and only with 6 evidence keys. | Review the 6-key counter-trend bar and the 0.75 ATR no-chase floor once ~2 weeks of paper decisions show how often they are the binding constraint. | 2 sessions |

**Outstanding on the operator:** rotate the Anthropic API key and GitHub PAT
that were exposed in journald (fixed 2026-09-17, values still need rotating).

Deviation, deliberate: every engine runs at the **5m close only** (no 1m-driven
CONFIRMING->TRADE_READY leg) - see `trading_bot/engine/shadow.py`. Positions ARE
managed on 1m closes (hard stop / targets).

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
| 18 | Backtest | [x] | M3 | `engine/research/backtest.py` through PaperLoop; model option chain (BS, spot-only); no-lookahead test. Real option-chain history accrues from `option_chain_snapshots` for a later, better model. |
| 19 | Journal / Dataset | [x] | M2 | All schema-v1 tables populated: signals (status), risk_decisions, executions, trade_results, kill_switch_events, option_chain_snapshots; `engine/rejections.py` §72 dataset. |
| 20 | EOD Analysis | [x] | M3 | `engine/eod.py` session summary + `engine/research/review.py` daily review (§71), rejected-signal analysis (§72). Nightly automation of the review is M4 observability. |
| 21 | Walk-Forward | [x] | M3 | `validation.walk_forward()` chronological windows + `coverage()` (§75). No fitting step by design (§84). |
| 22 | Monte Carlo | [x] | M3 | `validation.monte_carlo()` bootstrap: final-net/dd percentiles, ruin probability, streaks, adverse-execution stress (§76). |
| 23 | Paper Trading | [x] code, [ ] run | M2-M3 | `engine/paper_loop.py`; `TECH_MODE` is still SHADOW. The paper phase (4-12 weeks, 100+ valid opportunities, §78) has not started. |
| 24 | Shadow Strategies | [x] | M3 | SHADOW mode journals every family's decision; `TECH_SHADOW_STRATEGIES` lists families that stay journal-only in PAPER/LIVE (status valid, reason shadow_only_strategy); per-strategy kill switches. |
| 25 | Controlled Live Deployment | [ ] deferred | M4b | Live broker adapter, one lot, Rs.60-75 initial risk ladder (§80). Not built until `research_cli gates` passes on paper data and each strategy has a §83 promotion record. |
| 26 | Live Observability | [x] M4a | M4 | `engine/health.py` §87 monitor: edge-triggered alerts to Telegram (feed, data, engine stall, orders, latency, daily loss, heat, reconciliation, API/DB errors, breaker, RSS), JSON heartbeat every 60 s; `engine/recovery.py` §54 restart recovery; nightly `trading-bot-engine-review.timer` posts review + gates. Live-adapter metrics land with M4b. |
| 27 | Continuous Research | [x] tooling | M4 | `research_cli` metrics/review/gates/walkforward/montecarlo/backtest/counterfactuals; `strategy_versions` promotion records (§83). |
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

## M3 build order — validation (built 2026-09-18, four stacked branches)

| Step | Branch | Spec | Delivers |
|---|---|---|---|
| 1 | `feature/m3-metrics` | §77 §63 | `engine/research/metrics.py`: full metric set on net P&L, segmentation, fingerprint stats with a `reliable` flag |
| 2 | `feature/m3-backtest` | §73 §84 | `engine/research/backtest.py`: replay a date range through the SAME PaperLoop + paper broker; spot-only model option chain; no-lookahead test |
| 3 | `feature/m3-validation` | §75 §76 §79 §81 | walk-forward windows + coverage, Monte Carlo (dd/ruin/streaks/slippage stress), parameter sensitivity, execution parity, the four gates |
| 4 | `feature/m3-review` | §71 §72 §83 | daily review, rejected-signal analysis, underlying-only counterfactuals kept separate, promotion record -> `strategy_versions`; `python -m trading_bot.research_cli` |

M3 gate is not code: it is 4–12 weeks of PAPER sessions journaled to Postgres,
then `research_cli gates` reporting ALL GATES PASSED, then a human promotion
record per strategy (§83). Nothing in M3 changes engine behaviour.

## M4a — observability and recovery (built 2026-09-18, three stacked branches; live adapter deliberately NOT built)

| Step | Branch | Spec | Delivers |
|---|---|---|---|
| 1 | `feature/m4a-health` | §85 §86 §87 | `engine/health.py` HealthMonitor: edge-triggered alerts (fire / escalate / recover), Telegram under the existing rate limit, JSON heartbeat; journal write failures counted, never fatal |
| 2 | `feature/m4a-recovery` | §54 §44 §56 | `engine/recovery.py`: positions from filled legs, plan from the locked snapshot, tallies from trade results, kill switches re-applied, reconcile; unsafe -> trading kill |
| 3 | `feature/m4a-review-timer` | §69 §71 §79 §83 | `research_cli --telegram`; `trading-bot-engine-review.timer` 15:45 IST posts the daily review + 28-day gates; `TECH_SHADOW_STRATEGIES` (phase 24) |

M4b (the live broker adapter behind the three-switch gate, one lot, Rs.60-75
initial risk, §80) waits for `research_cli gates` to pass on real paper data and
a human promotion record per strategy (§83). Until then nothing under
`engine/` can reach an order endpoint, and tests/test_engine_no_orders.py
proves it on every CI run.

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
| Research / validation | `engine/research/` (metrics, backtest, validation, review), `research_cli.py` |
| Safety guard | `tests/test_engine_no_orders.py`, `tests/test_config_bounds.py` |
