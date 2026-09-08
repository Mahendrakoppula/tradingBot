-- Example research queries against the bot's S3-synced historical data.
-- Run via research/duckdb_connect.py's connect(), or `duckdb` CLI with
-- INSTALL httpfs; LOAD httpfs; CALL load_aws_credentials(); SET s3_region='ap-south-1';
--
-- These are TEMPLATES, not proven results - there are only days of real
-- data as of when this was written (2026-09-08). Re-run periodically as
-- history accumulates; don't trust small-sample output from these yet.

-- === Trades: win rate and P&L by exit reason, daily vs scalp ===
SELECT
    COALESCE(strategy, 'daily') AS strategy,
    reason AS exit_reason,
    COUNT(*) AS n_trades,
    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
    ROUND(100.0 * SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate_pct,
    ROUND(SUM(realized_pnl), 2) AS total_pnl,
    ROUND(AVG(realized_pnl), 2) AS avg_pnl
FROM read_json_auto('s3://trading-bot-deploy-396913392704/trading-bot/historical/trade_log.jsonl')
GROUP BY 1, 2
ORDER BY 1, 2;

-- === Daily journal: capital trend, skip reasons, scalp activity ===
SELECT
    date, starting_capital, ending_capital, total_pnl,
    losing_streak_days_at_end, scalp_enabled, scalp_total_pnl,
    len(decisions) AS n_daily_decisions, len(trades) AS n_daily_trades,
    len(scalp_decisions) AS n_scalp_decisions, len(scalp_trades) AS n_scalp_trades
FROM read_json_auto('s3://trading-bot-deploy-396913392704/trading-bot/historical/journal.jsonl')
ORDER BY date;

-- === Why entries got skipped (the daily strategy's own reasoning log) ===
SELECT date, d.underlying, d.action, d.reason
FROM read_json_auto('s3://trading-bot-deploy-396913392704/trading-bot/historical/journal.jsonl'),
     UNNEST(decisions) AS t(d)
WHERE d.action = 'skipped'
ORDER BY date;

-- === Option chain: PCR and per-strike IV over the day (NIFTY example) ===
-- Swap the date/underlying in the path glob as needed, or use * to span days.
SELECT time, underlying, spot, pcr, c.strike, c.option_type, c.ltp, c.oi, c.iv
FROM read_json_auto('s3://trading-bot-deploy-396913392704/trading-bot/historical/option_chain_log/NIFTY_*.jsonl'),
     UNNEST(contracts) AS t(c)
ORDER BY time, c.strike, c.option_type;

-- === Candle history: real per-minute OHLCV+OI for tracked near-ATM contracts ===
-- Each file is one contract+interval+day (candle_history_logger.py) - glob
-- across many at once. tradingsymbol/interval are in the filename AND the
-- JSON body, so either works for filtering.
SELECT tradingsymbol, interval, strike, option_type, updated_at, len(candles) AS n_candles
FROM read_json_auto('s3://trading-bot-deploy-396913392704/trading-bot/historical/candle_log/*.json');

-- Exploded per-minute rows for one contract+interval (adjust the glob):
SELECT tradingsymbol, interval, cd.time, cd.open, cd.high, cd.low, cd.close, cd.volume, cd.oi
FROM read_json_auto('s3://trading-bot-deploy-396913392704/trading-bot/historical/candle_log/NIFTY_*_ONE_MINUTE_*.json'),
     UNNEST(candles) AS t(cd)
ORDER BY cd.time;
