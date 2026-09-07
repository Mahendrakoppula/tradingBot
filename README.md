# trading-bot

Python bot for Angel One's SmartAPI. Entrypoints:

- `trading_bot/run_daily.py` — **the active strategy**: daily intraday
  long-option buying (NIFTY, BANKNIFTY, stock options). **Dry-run by
  default.**
- `trading_bot/run_condor.py` — a defined-risk iron condor (selling premium,
  hedged). **Dormant** - verified live that it needs real margin
  (Rs.40k-95k+ per lot even hedged) on every liquid name, which isn't viable
  below roughly Rs.2-3 lakh capital. Kept for when capital is raised.
- `trading_bot/main.py` — original skeleton: logs in, streams live ticks +
  order updates for one symbol, no strategy logic.
- `trading_bot/show_context.py` — prints a one-shot market-context snapshot
  (see below).

Full API reference: `docs/smartapi-reference.md`.

## Strategy: daily intraday long options (`run_daily.py`)

**Why buying, not selling:** the original design was a hedged iron condor
(sell premium), but verified live against a real account (2026-09-07) that
even hedged, margin runs Rs.40k-95k+ per lot on NIFTY, BANKNIFTY, and every
liquid stock checked (SBIN, RELIANCE, TATASTEEL, IDEA, ITC) - none of it fits
Rs.50k capital. A debit SPREAD was also tried and rejected: Angel's margin
calculator doesn't net the short leg against the long leg for a vertical
spread, so it costs MORE margin (~Rs.32k, verified live) than just buying
the single leg outright. So: **plain long options, no spread, no margin** -
max loss is simply the premium paid, which is what makes this work on small
capital.

- Universe: NIFTY, BANKNIFTY, and any stock options in `WATCHLIST`.
- This is a **daily** strategy, not near-expiry-only: any day the direction
  signal fires, it trades, using whatever the nearest contract is inside
  `[DTE_MIN, DTE_MAX]` (wide by default - 45 days, just a safety bound, not
  a gate).
- **Direction signal** (`debit_strategy.py`): primary is today's OI-buildup
  category for the underlying (Long/Short Built Up, Short Covering, Long
  Unwinding -> bullish/bearish lean). **Verified live that this only ever
  covers stock futures - NIFTY/BANKNIFTY never appear in it** (it's a
  top-10-per-category "movers" list, not a per-symbol lookup). Fallback,
  what actually drives index entries: simple momentum vs today's open
  (`MOMENTUM_MIN_MOVE_PCT`). Both are first-cut heuristics, not backtested.
- Entry: fixed time-of-day (`ENTRY_TIME`), first checking the direction
  signal above, then sizing against budget.
- **Sizing has no margin call at all** - just `premium_per_lot = LTP *
  lotsize`, sized to `min(RISK_PER_TRADE_PCT, MAX_CAPITAL_PCT_PER_TRADE) *
  current capital`, capped by `MAX_LOTS_PER_TRADE`. Skips the trade if even
  1 lot doesn't fit the budget (verified live: this is common for pricier/
  further-dated contracts like BANKNIFTY on a small budget - only cheap,
  fairly deep-OTM, near-expiry contracts fit a few-hundred/thousand-rupee
  budget, which is itself a real tradeoff worth knowing: those are
  low-probability "lottery ticket" style trades by nature of being cheap).
- Sizing IS the risk cap here: since max loss = premium paid, right-sizing
  at entry already bounds the trade to `RISK_PER_TRADE_PCT` of capital - the
  stop-loss check is just a backstop, not the primary risk control (unlike
  the condor, where stop-loss matters more).
- Hold: **same-day only (intraday).** Closed at `EXIT_TIME` or on hitting
  the stop-loss, never carried overnight - sidesteps stock-option
  physical-settlement risk and overnight gap risk.
- Capital: **paper capital of `CAPITAL` (default Rs.50,000)**, tracked as a
  running ledger in `.state/capital.json` that compounds with every closed
  trade's realized P&L - so paper performance persists day to day instead of
  resetting.
- Daily loss cap: blocks new entries for the rest of the day once breached,
  as a percentage of the current capital ledger.
- Every closed trade is appended to `.state/trade_log.jsonl` for reviewing
  how the strategy actually performed once you've let it paper-trade a while.
- Orders are placed as MARKET orders - slippage on thin far-OTM contracts is
  a real, currently-unmitigated risk.
- Position state survives a restart via `.state/long_positions.json`.

**Verified against a live account (2026-09-07, read-only - no orders
placed):** login/TOTP, instrument resolution, live spot pricing, strike
selection, and sizing all work correctly end-to-end. Two real bugs were
found and fixed this way: (1) the OIBuildup/gainersLosers endpoints are
rate-limited to ~1 req/sec (undocumented) - calling them back-to-back got
HTTP 403 on most calls; fixed by pacing and fetching once per cycle instead
of once per underlying. (2) OIBuildup never includes indices - the momentum
fallback above is what fixes that. Nothing has been run with real order
placement yet (`DRY_RUN` stays `true`).

## Morning briefing

At startup, `run_daily.py` builds and sends one Telegram message via
`briefing.build_morning_briefing()`: India VIX, NIFTY50 and BANKNIFTY market
breadth (advancing/declining/flat counts, top gainers/losers, top volume
names - see `breadth.py`), and global cues (S&P 500, Dow, Nasdaq, WTI crude,
USD/INR). Verified live end-to-end (2026-09-07) - correctly resolved all 50
NIFTY50 + 13 BANKNIFTY constituents against the real scrip master and pulled
real quotes. **One-shot at startup only, not refreshed through the day** -
extending it to periodic updates or wiring breadth into entry decisions is a
natural next step, not done yet. Constituent lists are hardcoded (indices
rebalance semi-annually - a missing/renamed name just logs a warning and is
skipped, doesn't crash, but re-verify the lists periodically).

## Alerts

`trading_bot/notifier.py` pushes a Telegram message on: startup, the morning
briefing, every entry, every close (with P&L and reason), the daily loss cap
being hit, and any close that failed (flagged as needing manual attention).
Set `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` in `.env` - see
`deploy/DEPLOY.md` step 1 for how to get them. Leaves quietly (just logs) if
unconfigured.

## Deploying to AWS

**Live as of 2026-09-07** - see `deploy/DEPLOY.md` for the full guide.
Architecture: one EC2 `t3.micro` in `ap-south-1`, started 6am IST / stopped
8pm IST on weekdays by EventBridge Scheduler, running the bot as a systemd
service. No SSH/inbound ports - managed entirely through AWS Systems
Manager. Real credentials live in **AWS SSM Parameter Store** (free tier),
fetched fresh onto the instance on every boot - never bundled into the
deploy package, never touch S3 as plaintext, never touch git. Non-secret
strategy config (`deploy/config.env`) IS committed and IS part of the
deploy package. Estimated cost: ~$4-5/month.

**CI/CD**: push/merge to `main` on GitHub triggers `.github/workflows/deploy.yml`
- runs `pytest tests/`, and only if that passes, packages, uploads to S3,
and redeploys onto the live instance (auto start/stop if it's outside the
6am-8pm window). Auth via GitHub OIDC federation to a scoped IAM role - no
static AWS keys stored in GitHub. Workflow: make changes on a branch, merge
to main, CI ships it.

Real bugs found and fixed post-deploy by actually checking the running
instance, not just trusting the scripts: (1) the systemd service was still
pointed at the old `run_condor` module from before the buying pivot - the
deploy artifact never got updated when the strategy changed. (2)
**Critical:** the instance's system clock is UTC, not IST - naive
`datetime.now()` calls would have silently made `ENTRY_TIME=12:30` mean
6:00pm IST, hours after market close, with no error. Fixed by adding
`trading_bot/timeutil.py` (`now_ist()`, a fixed UTC+5:30 offset - not
`zoneinfo`, to avoid depending on the host having IANA tzdata installed) and
using it everywhere both runners compare against `ENTRY_TIME`/`EXIT_TIME` or
check for a new trading day. (3) A Git-Bash-vs-native-Windows-AWS-CLI path
translation bug (`/tmp/...` resolves differently to each) silently broke a
mid-script `aws iam put-role-policy` call - fixed by using a repo-local temp
dir instead of `/tmp`.

## Market context (`market_context.py`)

Broader market-state data, separate from the strategy's own OI-buildup/
momentum signal - run `python -m trading_bot.show_context` to see a
snapshot:

- **Market-wide breadth/sentiment** - India VIX, Put-Call Ratio, OI buildup,
  OI-based gainers/losers. All from SmartAPI itself, no extra key needed.
- **Global cues** - S&P 500, Dow, Nasdaq, WTI crude, USD/INR, via Yahoo
  Finance's unofficial (keyless, undocumented) quote endpoint. Best-effort.
- **Macro** - India CPI inflation and GDP growth, via the World Bank API
  (free, keyless, but annual/lagged - background only).
- **News headlines** and **economic calendar** - need a free API key you
  provide (`NEWS_API_KEY` from newsapi.org, `FINNHUB_API_KEY` from
  finnhub.io). Skipped gracefully if unset.

## Selling premium later (`run_condor.py`, dormant)

If capital is raised enough to sell premium again (see the real margin
figures above - realistically Rs.2-3 lakh+ to trade even 1 lot on a cheap
name with a safety buffer), `run_condor.py` implements a hedged iron condor
using `market_filter.TradeFilter` (VIX/PCR/OI bands) as its entry gate.
Fully built and unit-tested, just not capital-appropriate right now.

## Setup

1. Copy `.env.example` to `.env` and fill in your API key, client code, PIN,
   and TOTP secret (from Enable TOTP on the SmartAPI site - it's the base32
   secret behind the QR code). Review the strategy settings too - the
   defaults are placeholders, not tuned recommendations.
2. `python -m venv .venv && .venv\Scripts\pip install -r requirements.txt`
3. Tick watcher: `.venv\Scripts\python -m trading_bot.main --exchange NSE --symbol SBIN-EQ`
4. Daily long-option strategy (stays paper/dry-run until you set
   `DRY_RUN=false` AND `ENABLE_TRADING=true`):
   `.venv\Scripts\python -m trading_bot.run_daily`

## Layout

- `config.py` — env-based config: credentials, local/public IP + MAC address
  for the required auth headers, and all strategy parameters
- `auth.py` — login (client code + PIN + TOTP), token refresh, logout
- `rest_client.py` — REST endpoint wrapper; order-placing calls are
  dry-run-guarded
- `instruments.py` — downloads/caches the daily scrip master, resolves
  symbol -> token
- `options.py` — filters the scrip master into an underlying's option chain;
  resolves nearest expiry / nearest strike; resolves index & equity spot
  instruments. Schema verified against a live scrip-master dump, not just
  the docs.
- `strategy.py` — shared order-placement helpers (freeze-quantity splitting,
  LTP lookup) plus `IronCondorStrategy` (dormant, see above)
- `debit_strategy.py` — `LongOptionStrategy` (active) plus the OI-buildup/
  momentum direction signal
- `sizing.py` — `size_long_option` (premium-based, active) and
  `size_condor` (margin-based, dormant)
- `risk.py` — per-trade stop and daily loss-cap gate, both a percentage of
  the running capital ledger
- `state.py` — persists open positions (condor and long-option, separately),
  the capital ledger, and a trade log to disk so a restart doesn't lose
  track of any of it
- `run_daily.py` — **the active strategy loop**
- `run_condor.py` — the dormant condor strategy loop
- `market_filter.py` — VIX+PCR+OI go/no-go check used by `run_condor.py`
- `ws_market.py` — WebSocket Streaming 2.0 client, parses the binary tick
  protocol
- `ws_orders.py` — WebSocket Order Status client (JSON order updates)
- `main.py` — the original tick/order-status watcher (no strategy)
- `market_context.py` — market-wide breadth (VIX/PCR/OI), global cues,
  macro data, and (key-gated) news/economic calendar
- `show_context.py` — CLI that prints one `market_context` snapshot
- `breadth.py` — NIFTY50/BANKNIFTY constituent resolution + batch quotes +
  advance/decline/volume-leader summary, see "Morning briefing" above
- `briefing.py` — combines `market_context` + `breadth` into the one
  Telegram message sent at startup
- `timeutil.py` — `now_ist()`/`today_ist()`, IST-aware time helpers (fixed
  UTC+5:30 offset) - use these, never naive `datetime.now()`, for anything
  compared against `ENTRY_TIME`/`EXIT_TIME` or day-rollover checks
- `notifier.py` — Telegram push notifications, see "Alerts" above
- `error_notifier.py` — a SEPARATE Telegram bot/chat dedicated to error
  alerts (date-tagged), so real problems don't get lost in routine activity
  notifications
- `deploy/` — AWS deployment artifacts: systemd units, EC2 provisioning
  script, EventBridge start/stop schedules, redeploy/teardown scripts,
  SSM-Parameter-Store secret bootstrap (`fetch_secrets.sh`), non-secret
  strategy config (`config.env`), GitHub OIDC/IAM policies for CI - see
  `deploy/DEPLOY.md`
- `.github/workflows/deploy.yml` — CI: tests, then auto-deploys on push to
  `main`
- `tests/` — pytest suite covering strategy logic, sizing, risk caps, state
  persistence, the market filter, and the direction signal - runs in CI
  before every deploy

## Not built yet

- Backtesting the direction signal / thresholds against historical data
  (they're unvalidated starting guesses right now)
- Limit-order execution with depth-aware pricing (currently MARKET orders)
- Trade/fill persistence beyond the current day's open positions
- Any LIVE order placement at all - login/data/sizing verified live, but
  `DRY_RUN` has stayed `true` throughout
