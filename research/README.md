# Research: ORB strategy backtest (spot-proxy)

Standalone research/backtesting area, separate from the live `trading_bot/`
package, `deploy/`, and `.github/workflows/` - nothing here is imported by
or affects the production bot. Intended to move to its own
instance/codebase if/when this becomes a real second strategy.

## What was pulled

`fetch_historical.py` logs into the real SmartAPI account (reuses
`trading_bot/auth.py`, `config.py`, `rest_client.py` - no new auth code) and
pulls, via `getCandleData`:

- **NIFTY** and **BANKNIFTY** spot/index candles (tokens `99926000` /
  `99926009`, exchange `NSE`)
- **ONE_MINUTE**, last 9 months (2025-12-12 -> 2026-09-08): ~68,000 candles
  per symbol
- **ONE_DAY**, last 5 years (2021-09-09 -> 2026-09-08): 1,239 candles per
  symbol

Cached as CSV under `research/data/` (gitignored - re-run
`fetch_historical.py` to regenerate; not committed since it's ~140k rows of
raw market data, easy to reproduce, no reason to bloat the repo).

**Rate limiting note:** the documented limit for `getCandleData` is 3
req/sec, but empirically even the very first call after a fresh login got a
`403 Access denied because of exceeding access rate` from a cold start.
Real behavior is stricter/flakier than documented - `fetch_historical.py`
paces at ~1 req/1.2s with retry+backoff, and still saw occasional 403s that
resolved on retry. Budget for this if extending the pull.

## Key finding: historical OPTION premium data is not obtainable

This is the load-bearing finding for what's backtestable at all.
`trading_bot/instruments.py` downloads Angel One's scrip master, which is
regenerated daily and **only ever contains currently-live (unexpired)
contracts** - confirmed empirically: filtering today's scrip master for
NIFTY options showed the earliest listed expiry was *today itself*, nothing
in the past. There's no documented archived/historical scrip master
endpoint. Since resolving a `symboltoken` requires the scrip master, and
`getCandleData` requires a `symboltoken`, **there is no way to pull
historical premium candles for an option that has already expired** through
this API - regardless of whether `getCandleData` itself would technically
serve that data if you had the token (docs only explicitly restrict the
separate `getOIData` endpoint to "live F&O contracts"; `getCandleData` has
no such documented restriction, but it's moot without a token).

Practical implication: **Zerodha's actual ORB-on-premium strategy (enter
on the ₹200-ish premium option's own breakout) cannot be backtested with
real historical data through this API.** The only way to build a real
premium-based backtest going forward is to start *logging* the live bot's
own option-chain observations daily from here on, and accumulate history
that way - there's no shortcut to backfilling it.

## What was actually backtested instead

`backtest_orb.py` implements a **spot-price proxy**: the same ORB
mechanics (09:15-11:15 reference range, breakout-of-range-close entry,
opposite-side-of-range stop, 15:15 time exit - matching the live bot's own
`EXIT_TIME`) applied to the **underlying's own spot price**, not an option
premium. This tests only the *directional signal* - does a morning range
breakout predict continued directional movement by 15:15? - not real
option economics.

**This does NOT model:** option premium, strike selection, theta decay, IV
changes/crush, bid-ask spread, brokerage, STT, or slippage. A result here
says something about the raw directional edge of the ORB heuristic; it says
nothing about whether buying an actual option on that signal would be
profitable after real option-market frictions.

Lookahead-bias check performed: the reference window strictly precedes
the entry-scanning window (09:15-11:15 vs. >11:15); breakout is detected on
a candle's *close* but filled at the *next* candle's *open*, not the same
candle's close, so the fill price was never available at signal time.

## Results (9 months, 2025-12-12 to 2026-09-08)

| | NIFTY | BANKNIFTY |
|---|---|---|
| Days with valid ref window | 182 | 182 |
| Trades taken | 165 | 155 |
| Win rate | 49.7% (82W/83L) | 54.2% (84W/71L) |
| Avg win | 0.335% | 0.404% |
| Avg loss | -0.300% | -0.375% |
| Mean return/trade | 0.015% | 0.047% |
| Stdev/trade | 0.448% | 0.531% |
| Cumulative return (compounded, no sizing/costs) | 2.36% | 7.34% |
| Max drawdown | 4.72% | 5.44% |
| Trade-count-scaled Sharpe-like | 0.43 | 1.10 |
| Stop-hit / time-exit | 19 / 146 | 19 / 136 |

**Read honestly:** NIFTY is statistically close to a coin flip with a
thin positive drift - not a result I'd trust to survive real transaction
costs (brokerage, STT, spread on the actual option) even if converted to a
real premium trade. BANKNIFTY shows more edge (54.2% win rate, better
Sharpe-like ratio) but on a similarly small sample (155 trades) and a more
volatile underlying (higher stdev/trade) - promising enough to be worth
tracking forward, not strong enough to act on directly. Neither number
includes any transaction costs, and both are spot returns, not the
leveraged, theta-decaying payoff of an actual long option - the real
option version could look meaningfully better OR meaningfully worse
depending on strike/IV dynamics this backtest cannot see.

## Suggested next steps (not implemented here)

1. Start logging real option-chain snapshots from the live bot going
   forward (a cheap addition - it already fetches this data for its own
   direction signal) so a real premium-based backtest becomes possible in
   a few months rather than never.
2. If pursuing this further before that data exists, extend the spot-proxy
   backtest with a simple options-pricing overlay (Black-Scholes off the
   spot moves, some assumed IV) to get a rougher-but-more-honest estimate
   of premium-based P&L than presenting spot returns as a stand-in.
3. Re-run this backtest periodically as more spot data accumulates - 9
   months / ~165 trades is a thin sample for the win-rate/Sharpe numbers
   above to be stable.

## Update (2026-09-08): suggestion #1 done - live data collection is running

The live bot now logs, going forward (nothing here backfills history -
same "can't get the past, only the future" limitation as this whole
document is about):

- **Option-chain snapshots** (`option_chain_logger.py`, every 5 min during
  market hours) - point-in-time LTP/OI/volume/depth for strikes within
  +-15% of spot, PLUS per-strike implied volatility (Option Greeks
  endpoint) and the underlying's PCR, added specifically to eventually
  support an IV-rank and PCR-trend signal (both need real accumulated
  history first - there isn't enough yet).
- **Real OHLCV+OI candles** (`candle_history_logger.py`, 1/5/10/30-min +
  daily, via getCandleData/getOIData - NOT point samples) for a small
  FIXED band of near-ATM strikes each day. This is the actual fix for the
  "SmartAPI has no historical option data" problem above, for anything
  traded from 2026-09-08 onward: as long as a contract is logged BEFORE it
  expires, its candle history is captured and durable (synced to S3 - see
  below), unlike trying to backfill an already-expired contract's history
  after the fact (impossible, per the finding above).

All of this lands on the EC2 instance's local disk under `.state/`, then
gets synced to S3 every 30 min (`deploy/sync_state_to_s3.sh`) at
`s3://trading-bot-deploy-396913392704/trading-bot/historical/` - durable,
survives an instance replacement.

### Querying it: DuckDB directly against S3, no ETL

`research/duckdb_connect.py` gives a one-line connection; DuckDB reads the
raw JSON/JSONL files straight off S3 (verified live 2026-09-08, including
`UNNEST()` over nested arrays like `contracts` and `candles`) - no
conversion pipeline, no running database, no ongoing cost. See
`research/example_queries.sql` for win-rate/P&L/IV/PCR/candle query
templates.

Deliberately NOT converting to Parquet yet - premature at days of data.
Revisit only if query speed actually becomes a real problem once there are
months of accumulated history, not before.
