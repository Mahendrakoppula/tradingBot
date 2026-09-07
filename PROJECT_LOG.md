# Project log

A narrative history of decisions and why they were made — complements
`README.md` (what/how) and `deploy/DEPLOY.md` (deployment specifics). Meant
so a future session (human or AI) can pick this project back up without
re-deriving context. Chronological; most recent at the bottom of each
section. No credentials or account-specific IDs appear here on purpose —
those live in `.env` (gitignored) and AWS SSM Parameter Store.

## Origin

Started by reading the full Angel One SmartAPI documentation before writing
any code, and saving a structured reference (`docs/smartapi-reference.md`)
so implementation could be grounded in the real API surface rather than
assumption. That reference has since been corrected in several places
against live behavior (see "Corrections found by testing live" below) —
docs and reality disagreed on some real points.

## Strategy evolution

1. **Iron condor, near-expiry only, ₹50k capital.** The original design:
   sell a hedged iron condor (defined risk via wings, not just a stop-loss)
   on NIFTY/BANKNIFTY/stock options, only within a few days of expiry.
2. **Pivoted to daily, not near-expiry-only.** Proximity to expiry was
   dropped as the entry gate in favor of a market-condition filter — the
   DTE window became a wide safety bound (45 days) rather than the trigger.
3. **Pivoted from selling to buying.** Tested the hedged condor against a
   real account and found margin requirements of ₹40,000–95,000+ per lot on
   every liquid NIFTY/BANKNIFTY/stock name checked — none of it fits ₹50k
   capital, even hedged. A debit spread was tried too and rejected: the
   broker's margin calculator doesn't net a vertical spread's capped risk,
   so it actually costs *more* margin than a naked long option. Landed on
   **plain single-leg long options** — max loss is simply the premium paid,
   which is what makes small capital viable at all. The condor code
   (`run_condor.py`, `market_filter.py`) stays in the repo, dormant, for
   whenever capital is raised enough to sell premium again (realistically
   ₹2–3 lakh+).
4. **Direction signal, and its real limits.** Primary signal is today's
   NSE OI-buildup category for the underlying — but verified live that this
   endpoint *only* returns stock futures, never indices (it's a top-10
   "movers" screener, not a per-symbol lookup), so NIFTY/BANKNIFTY can never
   get a signal from it. Added a momentum-vs-today's-open fallback
   specifically so indices get a signal at all.
5. **Added a genuinely leading signal.** Everything above reacts to price
   that has *already* moved (lagging by construction). Added
   `premarket_bias.py`: a once-a-day, before-market-opens read of the prior
   US session's close, India VIX, and today's economic calendar — used both
   to inform (posted to Telegram) and to gate entries (must agree with the
   day's bias; a CAUTIOUS day blocks all new entries).
6. **Added a bounded, safe form of "learning."** Explicitly did NOT build
   auto-tuning of entry thresholds from a few days of paper-trading data —
   that risks fitting noise, not finding a real edge. Instead: a structured
   daily journal (conditions, decisions, outcomes) meant for periodic human
   review, plus one narrow automatic safety behavior — a losing-streak
   circuit breaker that shrinks position size after N consecutive losing
   days until a human looks at the journal and decides on a real change.

## Corrections found by testing live, not by reading docs

- The scrip master's `exch_seg` field is a plain code (`NSE`, `NFO`, ...) —
  the docs' own Instruments page implies `nse_cm`-style names, which
  actually belong to the WebSocket subscription field, a different thing.
- `strike` in the scrip master is the real strike × 100.
- Index spot rows use `instrumenttype: "AMXIDX"`; equity spot rows use
  `instrumenttype: ""` with a `-EQ` symbol suffix.
- BANKNIFTY and stock options currently have monthly-only expiries; only
  NIFTY still has weekly ones (this can change — NSE has changed it before).
- `OIBuildup`/`gainersLosers` are rate-limited to roughly 1 req/sec —
  undocumented, discovered via HTTP 403s when called back-to-back.
- Margin Calculator doesn't fully net a hedged structure's risk the way
  you'd expect from a real exchange — see strategy evolution step 3.
- Market Data Quote API's `OHLC` mode omits `tradeVolume`; `FULL` mode has
  it. Needed for the breadth feature (advance/decline across NIFTY50 +
  BANKNIFTY constituents).
- Global-cue quotes (Yahoo Finance, unofficial/keyless) expose
  `regularMarketChangePercent` directly — used as the leading "overnight US
  move" input to the pre-market bias, no need to compute it manually.

## Infrastructure history

- **AWS deployment**: one EC2 instance in `ap-south-1`, started 8am IST /
  stopped 6pm IST on weekdays via EventBridge Scheduler, managed entirely
  through SSM (no SSH, no inbound security-group rules at all).
- **Two real bugs found only by checking the live instance**, not by
  trusting the deploy scripts: the systemd service was still pointed at the
  old selling-strategy module after the buying pivot (deploy artifacts
  don't update themselves when strategy code changes); and the instance's
  system clock is UTC, not IST — naive `datetime.now()` would have silently
  made `ENTRY_TIME=12:30` mean 6pm IST, hours after close, with no error at
  all. Fixed with `trading_bot/timeutil.py` (a fixed UTC+5:30 offset, not
  `zoneinfo`, so it doesn't depend on the host having IANA tzdata).
- **Secrets moved out of the deploy package entirely.** Originally bundled
  a `.env` into the S3 zip. Rejected AWS Secrets Manager early to avoid its
  flat monthly fee. Once CI/CD needed to deploy code without ever handling
  secrets, moved real credentials to **SSM Parameter Store** (free tier) —
  fetched fresh onto the instance on every boot by a bootstrap systemd
  service. Non-secret strategy config (`deploy/config.env`) stayed in git,
  since editing it and merging to main is exactly how you're meant to
  change production behavior without touching code.
- **CI/CD**: push/merge to `main` on GitHub runs the test suite, and only
  if that passes, packages, uploads, and redeploys onto the live instance
  (starting/stopping it automatically if the push lands outside the
  scheduled window). Authenticates via GitHub OIDC to a narrowly-scoped IAM
  role — no static AWS keys stored in GitHub.
- **Two dedicated Telegram bots**: one for routine activity (entries,
  exits, the morning briefing), one purely for error alerts so real
  problems don't get lost in the noise — every error message is tagged
  with the date.
- A Windows-machine-specific gotcha worth remembering for any future work
  here: the pip-installed AWS CLI is native Windows Python, not
  MSYS/Git-Bash-aware — a file bash writes to `/tmp/foo` is invisible to it
  (`/tmp` resolves differently to each). Any `file://` path passed to `aws`
  from a bash script on this machine must be a repo-relative path, never
  `/tmp/...`.

## What's genuinely still open

- No live order has ever been placed — `DRY_RUN` has stayed `true`
  throughout everything above. Flipping that (and `ENABLE_TRADING`) is a
  deliberate decision to make together, not something to do quietly.
- The direction signal, momentum threshold, and pre-market bias thresholds
  are first-cut heuristics — stated as such in their own docstrings — not
  backtested against historical data.
- At ₹50k capital with a 2% per-trade risk budget, only cheap (often
  deep-OTM and/or near-expiry) options actually fit the budget — those are
  structurally low-probability trades by nature of being cheap. Worth
  watching in the journal once real activity accumulates.
- Periodic/continuous breadth or bias refresh through the day isn't built —
  both are currently once-per-day, at startup.
- The daily journal (`​.state/journal.jsonl`) is brand new as of this
  writing — there isn't yet enough data in it to make any real decision
  from. Come back to it after it's accumulated a couple of weeks of
  trading days.

## How to pick this back up in a future session

1. Read `README.md` for what's built and how it fits together, this file
   for why, and `deploy/DEPLOY.md` for operating it.
2. Check `.state/journal.jsonl` and `.state/trade_log.jsonl` (both
   gitignored — pull them from the live instance via SSM if reviewing
   remotely) for what's actually happened since this was written.
3. Anything not mentioned as "still open" above should be treated as
   working and tested, not aspirational — this project has been built with
   a habit of verifying against the real account/live instance rather than
   assuming, and that habit is worth keeping up.
