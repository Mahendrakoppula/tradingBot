"""Second bot: pure technical-indicator strategy (scalp/intraday/swing), a
fully independent OS process from run_daily.py - see
.claude/plans/goofy-plotting-sedgewick.md for the full design.

Shares the SAME SmartAPI credentials/api_key as run_daily.py (Angel One
issues one key per ACCOUNT, not per app - confirmed live) but its own
Session/login, own capital ledger (TECH_CAPITAL, separate 50k pool), own
kill switches (TECH_DRY_RUN/TECH_ENABLE_TRADING). A session-invalidation
race between the two processes is tolerated via rest_client.py's
auto-relogin-and-retry-once (mutating order calls deliberately do NOT
auto-retry - see that module).
"""
import argparse
import dataclasses
import datetime as dt
import logging
import signal
import time
from html import escape as esc

import requests

from trading_bot import costs as costs_mod
from trading_bot import state as state_mod
from trading_bot.auth import Session
from trading_bot.config import Config
from trading_bot.debit_strategy import LongOptionStrategy, build_long_leg
from trading_bot.equity_strategy import EquityDeliveryStrategy
from trading_bot.instruments import InstrumentLookup
from trading_bot.liquidity import check_liquidity, get_quote_for_contract
from trading_bot.options import OptionChain, find_spot_instrument
from trading_bot.rest_client import ApiError, RestClient
from trading_bot.risk import DailyRiskTracker
from trading_bot.sizing import size_equity_shares, size_long_option
from trading_bot.stock_screener import build_universe
from trading_bot.support_resistance import classic_pivot_points
from trading_bot.technical_config import TechnicalConfig
from trading_bot.technical_notifier import notify, notify_error
from trading_bot.technical_strategy import (
    atr_stop_target,
    candles_from_rows,
    intraday_signal,
    premium_stop_target,
    relative_strength_confirmed,
    scalp_signal,
    stop_breached,
    swing_should_exit,
    swing_signal,
    target_reached,
    update_trailing_stop,
)
from trading_bot.timeutil import now_ist, today_ist

log = logging.getLogger("trading_bot.technical")

POLL_SECONDS = 30

# getCandleData is documented at 3 req/sec/150/min, but research/fetch_historical.py
# found live that the real limiter is stricter/flakier than documented (the very
# first call after login got a 403 "exceeding access rate" from a cold start) -
# pace every daily-candle call in the swing scan the same way that script does,
# since it can fire dozens of these back to back across the stock universe.
CANDLE_CALL_SLEEP_SECONDS = 1.2
CANDLE_FETCH_RETRIES = 6
CANDLE_FETCH_RETRY_BASE_SECONDS = 3.0


def _get_candle_data_with_retry(rest: RestClient, exchange: str, token: str, interval: str, fromdate: str, todate: str) -> list:
    """getCandleData failures get retried with backoff - confirmed live
    (smoke-tested 2026-09-09) that even the FIRST call right after a fresh
    login can come back as a plain-text 403 ("Access denied because of
    exceeding access rate") that resp.json() can't parse, raising a
    ValueError/JSONDecodeError, not just an ApiError - same finding
    research/fetch_historical.py already documented and retries around
    (`_fetch_chunked`). Without this, a single transient rate-limit hiccup
    silently reads as "no signal today" for that underlying, forever - a
    real gap for a strategy whose whole point is to trade multiple times a
    day. Returns [] (no candles) only after every retry is exhausted."""
    for attempt in range(CANDLE_FETCH_RETRIES):
        try:
            return rest.get_candle_data(exchange, token, interval, fromdate, todate)
        except (ApiError, ValueError, requests.exceptions.RequestException) as e:
            log.warning("getCandleData failed (attempt %d/%d) for %s %s %s->%s: %s",
                        attempt + 1, CANDLE_FETCH_RETRIES, token, interval, fromdate, todate, e)
            time.sleep(CANDLE_FETCH_RETRY_BASE_SECONDS * (attempt + 1))
    log.error("Giving up on getCandleData for %s %s after %d attempts", token, interval, CANDLE_FETCH_RETRIES)
    return []


# --- messaging --------------------------------------------------------------


def _dry_run_badge(dry_run: bool) -> str:
    return "\U0001F9EA <i>DRY RUN</i>\n" if dry_run else ""


def _format_time(iso_str: str) -> str:
    """HH:MM:SS from an ISO timestamp (entered_at/ledger['updated_at'] are
    both now_ist().isoformat() strings) - falls back to the raw string if
    it doesn't parse, so a notification is never lost over a formatting
    edge case."""
    try:
        return dt.datetime.fromisoformat(iso_str).strftime("%H:%M:%S")
    except (ValueError, TypeError):
        return iso_str or "unknown"


def _entry_message(dry_run: bool, header: str, underlying: str, tradingsymbol: str, qty: int,
                    entry_price: float, cost: float, reason: str) -> str:
    return (
        f"{_dry_run_badge(dry_run)}\U0001F7E2 <b>{esc(header)} BOUGHT</b> {esc(underlying)} {esc(tradingsymbol)}\n"
        f"{qty} @ ~Rs.{entry_price:.2f}, cost Rs.{cost:.2f}\n"
        f"<i>Entered because:</i> {esc(reason)}"
    )


def _exit_message(dry_run: bool, header: str, underlying: str, tradingsymbol: str, pnl: float,
                   capital: float, entry_reason: str, exit_reason: str, entry_time: str, exit_time: str) -> str:
    dot = "\U0001F7E2" if pnl >= 0 else "\U0001F534"
    return (
        f"{_dry_run_badge(dry_run)}{dot} <b>{esc(header)} SOLD</b> {esc(underlying)} {esc(tradingsymbol)}\n"
        f"P&amp;L: Rs.{pnl:.2f} | Capital: Rs.{capital:.2f}\n"
        f"Entry: {esc(_format_time(entry_time))} | Exit: {esc(_format_time(exit_time))}\n"
        f"<i>Entered because:</i> {esc(entry_reason) or 'unknown'}\n"
        f"<i>Exited because:</i> {esc(exit_reason)}"
    )


def _parse_hhmm(hhmm: str) -> dt.time:
    return dt.datetime.strptime(hhmm, "%H:%M").time()


# --- price helpers ------------------------------------------------------


def _current_spot(rest: RestClient, instruments: InstrumentLookup, underlying: str) -> float:
    spot_row = find_spot_instrument(instruments.instruments, underlying)
    data = rest.get_ltp(spot_row["exch_seg"], spot_row["symbol"], spot_row["token"])
    return float(data["ltp"])


def _leg_ltp_and_pnl(rest: RestClient, leg: state_mod.LegFill) -> tuple[float, float]:
    data = rest.get_ltp(leg.exchange, leg.tradingsymbol, leg.symboltoken)
    ltp = float(data["ltp"])
    return ltp, (ltp - leg.entry_price) * leg.quantity


def _options_exchange_for(spot_row: dict) -> str:
    """The F&O segment an underlying's options trade on, derived from its
    spot instrument's own exchange - BSE indices (SENSEX) trade options on
    BFO, everything else (NSE indices/stocks) on NFO. Live-verified
    2026-09-09: SENSEX is exch_seg BSE for its spot AMXIDX row and BFO for
    its OPTIDX rows, same NSE-exch_seg-implies-NFO-options pattern already
    used for NIFTY/BANKNIFTY/stocks."""
    return "BFO" if spot_row.get("exch_seg") == "BSE" else "NFO"


def _atr_scaling_kwargs(cfg: TechnicalConfig) -> dict:
    """Scenario-adjustment knobs for atr_stop_target(), shared by every tier's entry."""
    return {
        "baseline_period_mult": cfg.atr_baseline_period_mult,
        "trend_fast_period": cfg.atr_trend_fast_period,
        "trend_slow_period": cfg.atr_trend_slow_period,
        "stop_scale_min": cfg.atr_stop_scale_min,
        "stop_scale_max": cfg.atr_stop_scale_max,
        "target_scale_min": cfg.atr_target_scale_min,
        "target_scale_max": cfg.atr_target_scale_max,
    }


# --- candle fetch/cache ------------------------------------------------


def _fetch_today_candles(rest: RestClient, exchange: str, token: str, interval: str, today: dt.date) -> list[dict]:
    fromdate = f"{today.isoformat()} 09:15"
    todate = now_ist().strftime("%Y-%m-%d %H:%M")
    rows = _get_candle_data_with_retry(rest, exchange, token, interval, fromdate, todate)
    return candles_from_rows(rows)


def _fetch_prev_day_ohlc(rest: RestClient, exchange: str, token: str, today: dt.date) -> tuple[float, float, float] | None:
    fromdate = f"{(today - dt.timedelta(days=10)).isoformat()} 09:00"
    todate = f"{(today - dt.timedelta(days=1)).isoformat()} 15:30"
    rows = _get_candle_data_with_retry(rest, exchange, token, "ONE_DAY", fromdate, todate)
    candles = candles_from_rows(rows)
    if not candles:
        return None
    last = candles[-1]
    return last["high"], last["low"], last["close"]


def _fetch_daily_candles(rest: RestClient, exchange: str, token: str, today: dt.date, days: int = 400) -> list[dict]:
    fromdate = f"{(today - dt.timedelta(days=days)).isoformat()} 09:00"
    todate = f"{(today - dt.timedelta(days=1)).isoformat()} 15:30"
    rows = _get_candle_data_with_retry(rest, exchange, token, "ONE_DAY", fromdate, todate)
    return candles_from_rows(rows)


def _refresh_candles(cache: dict, key: str, poll_seconds: int, fetch_fn) -> list[dict]:
    entry = cache.get(key)
    now_mono = time.monotonic()
    if entry is None or now_mono - entry[0] >= poll_seconds:
        candles = fetch_fn()
        cache[key] = (now_mono, candles)
        return candles
    return entry[1]


def _get_pivots(cache: dict, rest: RestClient, instruments: InstrumentLookup, underlying: str, today: dt.date) -> dict | None:
    if underlying in cache:
        return cache[underlying]
    spot_row = find_spot_instrument(instruments.instruments, underlying)
    ohlc = _fetch_prev_day_ohlc(rest, spot_row["exch_seg"], spot_row["token"], today)
    pivots = classic_pivot_points(*ohlc) if ohlc else None
    cache[underlying] = pivots
    return pivots


# --- close/settle (shared by all four position types) -------------------


def _option_cost(cfg: TechnicalConfig, entry_price: float, exit_price: float, quantity: int) -> float:
    return costs_mod.option_round_trip_cost(
        entry_price, exit_price, quantity, cfg.cost_brokerage_per_order, cfg.cost_stt_sell_pct,
        cfg.cost_exchange_txn_pct, cfg.cost_sebi_fee_pct, cfg.cost_stamp_duty_pct, cfg.cost_gst_pct,
    )


def _equity_cost(cfg: TechnicalConfig, entry_price: float, exit_price: float, quantity: int) -> float:
    return costs_mod.equity_round_trip_cost(
        entry_price, exit_price, quantity, cfg.cost_equity_brokerage_per_order, cfg.cost_equity_stt_pct,
        cfg.cost_exchange_txn_pct, cfg.cost_sebi_fee_pct, cfg.cost_equity_stamp_duty_pct, cfg.cost_gst_pct,
    )


def _settle_close(exit_fn, rest: RestClient, leg: state_mod.LegFill, risk: DailyRiskTracker, ledger: dict,
                   tag: str, underlying: str, reason: str, extra_fields: dict, journal: dict, journal_key: str,
                   header: str, cost_fn) -> None:
    ltp, gross_pnl = _leg_ltp_and_pnl(rest, leg)
    cost = cost_fn(leg.entry_price, ltp, leg.quantity)
    pnl = gross_pnl - cost
    try:
        exit_quote = get_quote_for_contract(rest, leg.exchange, leg.symboltoken)
    except Exception:
        log.exception("Could not fetch exit quote for %s - falling back to MARKET order", leg.tradingsymbol)
        exit_quote = None
    exit_fn(leg, quote=exit_quote)
    risk.record_realized(pnl)
    ledger["current_capital"] += pnl
    ledger["updated_at"] = now_ist().isoformat()
    state_mod.save_technical_capital(ledger)
    record = {
        "strategy": tag, "underlying": underlying, "closed_at": ledger["updated_at"], "reason": reason,
        "qty": leg.quantity, "gross_pnl": gross_pnl, "costs": cost, "realized_pnl": pnl,
        "capital_after": ledger["current_capital"],
    }
    record.update(extra_fields)
    state_mod.log_trade(record)
    journal[journal_key].append({"underlying": underlying, "pnl": pnl, "reason": reason})
    log.info("%s %s closed (%s): gross %.2f - costs %.2f = P&L %.2f, capital now Rs.%.2f",
              header, underlying, reason, gross_pnl, cost, pnl, ledger["current_capital"])
    notify(_exit_message(rest.session.cfg.dry_run, header, underlying, leg.tradingsymbol, pnl,
                          ledger["current_capital"], extra_fields.get("signal_reason", ""), reason,
                          extra_fields.get("entered_at", ""), ledger["updated_at"]), html=True)


# --- scalp tier -----------------------------------------------------------


RELATIVE_STRENGTH_BENCHMARK = "NIFTY"  # index-rotation framing - "is this index leading or lagging the broader market"


def _relative_strength_ok(cfg: TechnicalConfig, rest: RestClient, instruments: InstrumentLookup, candle_cache: dict,
                           poll_seconds: int, interval: str, today: dt.date, underlying: str, direction: str,
                           own_candles: list[dict]) -> bool:
    """Gates a BANKNIFTY/SENSEX signal on relative strength vs NIFTY (the
    benchmark) - see technical_strategy.relative_strength_confirmed's own
    docstring for the validated idea this adapts. NIFTY's own signals are
    exempt (no natural benchmark for the benchmark itself). Reuses the
    SAME per-tier candle_cache/interval/poll_seconds the caller already
    uses for its own candles, so when NIFTY is itself in the watchlist
    (it always is, currently) this adds no extra API calls - NIFTY's
    benchmark fetch here and its own entry-check fetch share one cache
    entry keyed by "NIFTY"."""
    if not cfg.require_relative_strength or underlying == RELATIVE_STRENGTH_BENCHMARK:
        return True
    benchmark_row = find_spot_instrument(instruments.instruments, RELATIVE_STRENGTH_BENCHMARK)
    benchmark_candles = _refresh_candles(
        candle_cache, RELATIVE_STRENGTH_BENCHMARK, poll_seconds,
        lambda: _fetch_today_candles(rest, benchmark_row["exch_seg"], benchmark_row["token"], interval, today),
    )
    own_closes = [c["close"] for c in own_candles]
    benchmark_closes = [c["close"] for c in benchmark_candles]
    return relative_strength_confirmed(direction, own_closes, benchmark_closes, cfg.relative_strength_lookback_bars)


def _maybe_scalp_enter(cfg: TechnicalConfig, rest: RestClient, instruments: InstrumentLookup, strategy: LongOptionStrategy,
                        risk: DailyRiskTracker, positions: dict, trade_counts: dict, underlying: str, today: dt.date,
                        candle_cache: dict, journal: dict) -> None:
    spot_row = find_spot_instrument(instruments.instruments, underlying)
    candles = _refresh_candles(
        candle_cache, underlying, cfg.scalp_poll_seconds,
        lambda: _fetch_today_candles(rest, spot_row["exch_seg"], spot_row["token"], "ONE_MINUTE", today),
    )
    option_type, reason = scalp_signal(candles, cfg.scalp_ema_fast, cfg.scalp_ema_slow, cfg.scalp_avg_volume_period, cfg.scalp_min_relative_volume)
    if option_type is None:
        return
    if not _relative_strength_ok(cfg, rest, instruments, candle_cache, cfg.scalp_poll_seconds, "ONE_MINUTE", today,
                                  underlying, option_type, candles):
        log.info("TECH SCALP %s: skipping entry - relative strength vs %s not confirmed for %s",
                  underlying, RELATIVE_STRENGTH_BENCHMARK, option_type)
        journal["scalp_decisions"].append({"time": now_ist().isoformat(), "underlying": underlying,
                                            "reason": "relative_strength_not_confirmed", "lots": 0})
        return
    spot = candles[-1]["close"]

    chain = OptionChain(instruments.instruments, underlying, exchange=_options_exchange_for(spot_row))
    expiry = chain.nearest_expiry_within(today, cfg.dte_min, cfg.dte_max)
    if expiry is None:
        log.info("TECH SCALP %s: no expiry within DTE window [%d, %d]", underlying, cfg.dte_min, cfg.dte_max)
        return
    contract = build_long_leg(chain, expiry, spot, option_type, cfg.otm_distance_pct)

    quote = get_quote_for_contract(rest, contract.exchange, contract.token)
    if quote is None:
        log.info("TECH SCALP %s: skipping entry - could not fetch a quote for %s", underlying, contract.tradingsymbol)
        return
    liquid, liquidity_reason = check_liquidity(quote, cfg.max_spread_pct, cfg.min_open_interest)
    if not liquid:
        log.info("TECH SCALP %s: skipping entry - %s illiquid (%s)", underlying, contract.tradingsymbol, liquidity_reason)
        return

    budget = min(risk.max_loss_per_trade(), risk.ledger["current_capital"] * cfg.max_capital_pct_per_trade)
    lots, premium_per_lot = size_long_option(rest, contract, budget, cfg.max_lots_per_trade)
    if lots < 1:
        log.info("TECH SCALP %s: skipping entry - budget Rs.%.2f can't cover 1 lot (premium Rs.%.2f)", underlying, budget, premium_per_lot)
        return

    leg = strategy.enter(contract, qty_lots=lots, quote=quote)
    stop_price, target_price = premium_stop_target(leg.entry_price, contract.lotsize, cfg.scalp_stop_rupees_per_lot, cfg.scalp_target_rupees_per_lot)
    positions[underlying] = state_mod.OpenTechnicalOption(
        underlying=underlying, expiry=expiry.strftime("%d%b%Y").upper(), entered_at=now_ist().isoformat(),
        tier="scalp", signal_reason=reason, entry_spot=spot, stop_price=stop_price, target_price=target_price, option=leg,
    )
    trade_counts[underlying] = trade_counts.get(underlying, 0) + 1
    journal["scalp_decisions"].append({"time": now_ist().isoformat(), "underlying": underlying, "reason": reason, "lots": lots})
    notify(_entry_message(cfg.dry_run, "TECH SCALP", underlying, contract.tradingsymbol, lots, leg.entry_price, premium_per_lot * lots,
                           f"{reason} (stop premium {stop_price:.2f}, target premium {target_price:.2f})"), html=True)


# --- intraday tier ---------------------------------------------------------


def _maybe_intraday_enter(cfg: TechnicalConfig, rest: RestClient, instruments: InstrumentLookup, strategy: LongOptionStrategy,
                           risk: DailyRiskTracker, positions: dict, trade_counts: dict, underlying: str, today: dt.date,
                           candle_cache: dict, pivots: dict | None, journal: dict) -> None:
    spot_row = find_spot_instrument(instruments.instruments, underlying)
    bars = _refresh_candles(
        candle_cache, underlying, cfg.intraday_poll_seconds,
        lambda: _fetch_today_candles(rest, spot_row["exch_seg"], spot_row["token"], "FIVE_MINUTE", today),
    )
    option_type, reason = intraday_signal(bars, pivots, cfg.intraday_ema_fast, cfg.intraday_ema_slow, cfg.intraday_rsi_period,
                                           cfg.intraday_avg_volume_period, cfg.intraday_min_relative_volume)
    if option_type is None:
        return
    if not _relative_strength_ok(cfg, rest, instruments, candle_cache, cfg.intraday_poll_seconds, "FIVE_MINUTE", today,
                                  underlying, option_type, bars):
        log.info("TECH INTRADAY %s: skipping entry - relative strength vs %s not confirmed for %s",
                  underlying, RELATIVE_STRENGTH_BENCHMARK, option_type)
        journal["intraday_decisions"].append({"time": now_ist().isoformat(), "underlying": underlying,
                                               "reason": "relative_strength_not_confirmed", "lots": 0})
        return
    spot = bars[-1]["close"]

    chain = OptionChain(instruments.instruments, underlying, exchange=_options_exchange_for(spot_row))
    expiry = chain.nearest_expiry_within(today, cfg.dte_min, cfg.dte_max)
    if expiry is None:
        log.info("TECH INTRADAY %s: no expiry within DTE window [%d, %d]", underlying, cfg.dte_min, cfg.dte_max)
        return
    contract = build_long_leg(chain, expiry, spot, option_type, cfg.otm_distance_pct)

    quote = get_quote_for_contract(rest, contract.exchange, contract.token)
    if quote is None:
        log.info("TECH INTRADAY %s: skipping entry - could not fetch a quote for %s", underlying, contract.tradingsymbol)
        return
    liquid, liquidity_reason = check_liquidity(quote, cfg.max_spread_pct, cfg.min_open_interest)
    if not liquid:
        log.info("TECH INTRADAY %s: skipping entry - %s illiquid (%s)", underlying, contract.tradingsymbol, liquidity_reason)
        return

    budget = min(risk.max_loss_per_trade(), risk.ledger["current_capital"] * cfg.max_capital_pct_per_trade)
    lots, premium_per_lot = size_long_option(rest, contract, budget, cfg.max_lots_per_trade)
    if lots < 1:
        log.info("TECH INTRADAY %s: skipping entry - budget Rs.%.2f can't cover 1 lot (premium Rs.%.2f)", underlying, budget, premium_per_lot)
        return

    leg = strategy.enter(contract, qty_lots=lots, quote=quote)
    stop_price, target_price = premium_stop_target(leg.entry_price, contract.lotsize, cfg.intraday_stop_rupees_per_lot, cfg.intraday_target_rupees_per_lot)
    positions[underlying] = state_mod.OpenTechnicalOption(
        underlying=underlying, expiry=expiry.strftime("%d%b%Y").upper(), entered_at=now_ist().isoformat(),
        tier="intraday", signal_reason=reason, entry_spot=spot, stop_price=stop_price, target_price=target_price, option=leg,
    )
    trade_counts[underlying] = trade_counts.get(underlying, 0) + 1
    journal["intraday_decisions"].append({"time": now_ist().isoformat(), "underlying": underlying, "reason": reason, "lots": lots})
    notify(_entry_message(cfg.dry_run, "TECH INTRADAY", underlying, contract.tradingsymbol, lots, leg.entry_price, premium_per_lot * lots,
                           f"{reason} (stop premium {stop_price:.2f}, target premium {target_price:.2f})"), html=True)


# --- swing tier -------------------------------------------------------


def _maybe_swing_index_enter(cfg: TechnicalConfig, rest: RestClient, instruments: InstrumentLookup, strategy: LongOptionStrategy,
                              risk: DailyRiskTracker, positions: dict, underlying: str, today: dt.date, journal: dict) -> None:
    spot_row = find_spot_instrument(instruments.instruments, underlying)
    daily = _fetch_daily_candles(rest, spot_row["exch_seg"], spot_row["token"], today)
    direction, broken_level, reason = swing_signal(daily, cfg.swing_sma_fast, cfg.swing_sma_slow, cfg.swing_rsi_period,
                                                     cfg.swing_left_right, cfg.swing_sr_tolerance_pct, cfg.swing_sr_min_touches)
    if direction is None:
        return
    option_type = "CE" if direction == "long" else "PE"
    spot = float(rest.get_ltp(spot_row["exch_seg"], spot_row["symbol"], spot_row["token"])["ltp"])
    atr_result = atr_stop_target(daily, direction, spot, cfg.atr_period, cfg.atr_stop_mult, cfg.atr_target_mult, **_atr_scaling_kwargs(cfg))
    if atr_result is None:
        log.info("TECH SWING %s: skipping entry - not enough daily history yet for an ATR-based stop/target", underlying)
        return
    stop_price, target_price, atr_value = atr_result

    chain = OptionChain(instruments.instruments, underlying, exchange=_options_exchange_for(spot_row))
    expiry = chain.nearest_expiry_within(today, cfg.swing_index_dte_min, cfg.swing_index_dte_max)
    if expiry is None:
        log.info("TECH SWING %s: no expiry within wide DTE window [%d, %d]", underlying, cfg.swing_index_dte_min, cfg.swing_index_dte_max)
        return
    contract = build_long_leg(chain, expiry, spot, option_type, cfg.otm_distance_pct)

    quote = get_quote_for_contract(rest, contract.exchange, contract.token)
    if quote is None:
        log.info("TECH SWING %s: skipping entry - could not fetch a quote for %s", underlying, contract.tradingsymbol)
        return
    liquid, liquidity_reason = check_liquidity(quote, cfg.max_spread_pct, cfg.min_open_interest)
    if not liquid:
        log.info("TECH SWING %s: skipping entry - %s illiquid (%s)", underlying, contract.tradingsymbol, liquidity_reason)
        return

    budget = min(risk.max_loss_per_trade(), risk.ledger["current_capital"] * cfg.max_capital_pct_per_trade)
    lots, premium_per_lot = size_long_option(rest, contract, budget, cfg.max_lots_per_trade)
    if lots < 1:
        log.info("TECH SWING %s: skipping entry - budget Rs.%.2f can't cover 1 lot (premium Rs.%.2f)", underlying, budget, premium_per_lot)
        return

    leg = strategy.enter(contract, qty_lots=lots, quote=quote)
    positions[underlying] = state_mod.OpenTechnicalSwingOption(
        underlying=underlying, expiry=expiry.strftime("%d%b%Y").upper(), entered_at=now_ist().isoformat(),
        direction=direction, broken_level=broken_level, entry_index_price=spot, stop_price=stop_price,
        target_price=target_price, atr_value=atr_value, favorable_extreme=spot, trailing_active=False,
        signal_reason=reason, option=leg,
    )
    journal["swing_decisions"].append({"time": now_ist().isoformat(), "underlying": underlying, "reason": reason, "lots": lots})
    notify(_entry_message(cfg.dry_run, "TECH SWING", underlying, contract.tradingsymbol, lots, leg.entry_price, premium_per_lot * lots,
                           f"{reason} (stop {stop_price:.2f}, target {target_price:.2f}, ATR {atr_value:.2f})"), html=True)


def _maybe_swing_equity_enter(cfg: TechnicalConfig, rest: RestClient, equity_strategy: EquityDeliveryStrategy,
                               risk: DailyRiskTracker, positions: dict, row: dict, today: dt.date, journal: dict) -> None:
    name, token, exchange, symbol = row["name"], row["token"], row["exch_seg"], row["symbol"]
    daily = _fetch_daily_candles(rest, exchange, token, today)
    direction, broken_level, reason = swing_signal(daily, cfg.swing_sma_fast, cfg.swing_sma_slow, cfg.swing_rsi_period,
                                                     cfg.swing_left_right, cfg.swing_sr_tolerance_pct, cfg.swing_sr_min_touches)
    if direction != "long":
        return  # no equity-shorting infra in this repo - a "short" signal on a stock is simply not actionable

    quote = None
    try:
        quote = get_quote_for_contract(rest, exchange, token)
    except Exception:
        log.exception("Could not fetch quote for %s - continuing without depth-aware pricing", symbol)
    if quote is not None:
        liquid, liquidity_reason = check_liquidity(quote, cfg.max_spread_pct, min_oi=0)  # no OI concept in cash equity
        if not liquid:
            log.info("TECH SWING EQUITY %s: skipping entry - %s illiquid (%s)", name, symbol, liquidity_reason)
            return

    price = float(rest.get_ltp(exchange, symbol, token)["ltp"])
    atr_result = atr_stop_target(daily, "long", price, cfg.atr_period, cfg.atr_stop_mult, cfg.atr_target_mult, **_atr_scaling_kwargs(cfg))
    if atr_result is None:
        log.info("TECH SWING EQUITY %s: skipping entry - not enough daily history yet for an ATR-based stop/target", name)
        return
    stop_price, target_price, atr_value = atr_result

    # Size off the ACTUAL ATR-derived stop distance (rupees/share risked if
    # stopped out), not an assumed fixed percentage - risking
    # max_loss_per_trade() across `shares` means shares*(price-stop) <=
    # max_loss_per_trade(). Also capped by swing_equity_budget_pct_per_trade
    # as a straight notional-exposure ceiling (a diversified book of many
    # small stock positions, unlike the concentrated options tiers).
    stop_distance = price - stop_price
    if stop_distance <= 0:
        log.info("TECH SWING EQUITY %s: skipping entry - non-positive ATR stop distance (price %.2f, stop %.2f)", name, price, stop_price)
        return
    max_shares_by_risk = int(risk.max_loss_per_trade() / stop_distance)
    max_shares_by_exposure = size_equity_shares(price, risk.ledger["current_capital"] * cfg.swing_equity_budget_pct_per_trade)
    shares = min(max_shares_by_risk, max_shares_by_exposure)
    if shares < 1:
        log.info("TECH SWING EQUITY %s: skipping entry - budget can't size even 1 share (risk-capped %d, exposure-capped %d) at Rs.%.2f",
                  name, max_shares_by_risk, max_shares_by_exposure, price)
        return

    leg = equity_strategy.enter(symbol, token, exchange, shares, quote=quote)
    positions[name] = state_mod.OpenTechnicalEquity(
        underlying=name, entered_at=now_ist().isoformat(), broken_level=broken_level, stop_price=stop_price,
        target_price=target_price, atr_value=atr_value, favorable_extreme=price, trailing_active=False,
        signal_reason=reason, equity=leg,
    )
    journal["swing_decisions"].append({"time": now_ist().isoformat(), "underlying": name, "reason": reason, "shares": shares})
    notify(_entry_message(cfg.dry_run, "TECH SWING EQUITY", name, symbol, shares, leg.entry_price, shares * leg.entry_price,
                           f"{reason} (stop {stop_price:.2f}, target {target_price:.2f}, ATR {atr_value:.2f})"), html=True)


def _run_swing_scan(cfg: TechnicalConfig, rest: RestClient, instruments: InstrumentLookup, strategy: LongOptionStrategy,
                     equity_strategy: EquityDeliveryStrategy, risk: DailyRiskTracker, ledger: dict,
                     option_positions: dict, equity_positions: dict, today: dt.date, journal: dict) -> None:
    """Runs once per day: exits (trend-reversal/max-hold) on every currently
    open swing position, then scans for new entries across the index
    watchlist (options) and the F&O-eligible/volume-filtered stock universe
    (equity CNC). Stop-loss safety checks run separately, every cycle - see
    main()'s loop."""
    for pos_index, (underlying, position) in enumerate(list(option_positions.items())):
        if pos_index > 0:
            time.sleep(CANDLE_CALL_SLEEP_SECONDS)
        try:
            spot_row = find_spot_instrument(instruments.instruments, underlying)
            daily = _fetch_daily_candles(rest, spot_row["exch_seg"], spot_row["token"], today)
            if not daily:
                continue
            latest_close = daily[-1]["close"]
            days_held = (today - dt.datetime.fromisoformat(position.entered_at).date()).days
            if swing_should_exit(position.direction, position.broken_level, latest_close, cfg.swing_level_reclaim_buffer_pct):
                reason = "trend_reversal"
            elif days_held >= cfg.swing_max_hold_days:
                reason = "max_hold"
            else:
                continue
            _settle_close(strategy.exit, rest, position.option, risk, ledger, "technical_swing_option", underlying, reason,
                          {"expiry": position.expiry, "option_type": position.option.tradingsymbol[-2:], "direction": position.direction,
                           "broken_level": position.broken_level, "entered_at": position.entered_at, "signal_reason": position.signal_reason},
                          journal, "swing_trades", "TECH SWING", lambda e, x, q: _option_cost(cfg, e, x, q))
            del option_positions[underlying]
        except Exception as e:
            log.exception("Failed swing exit check for %s - MANUAL INTERVENTION MAY BE NEEDED", underlying)
            notify_error(f"Failed swing exit check for {underlying} - MANUAL INTERVENTION MAY BE NEEDED - {e}")
    state_mod.save_technical_swing_option(option_positions)

    for pos_index, (underlying, position) in enumerate(list(equity_positions.items())):
        if pos_index > 0:
            time.sleep(CANDLE_CALL_SLEEP_SECONDS)
        try:
            spot_row = find_spot_instrument(instruments.instruments, underlying)
            daily = _fetch_daily_candles(rest, spot_row["exch_seg"], spot_row["token"], today)
            if not daily:
                continue
            latest_close = daily[-1]["close"]
            days_held = (today - dt.datetime.fromisoformat(position.entered_at).date()).days
            if swing_should_exit("long", position.broken_level, latest_close, cfg.swing_level_reclaim_buffer_pct):
                reason = "trend_reversal"
            elif days_held >= cfg.swing_max_hold_days:
                reason = "max_hold"
            else:
                continue
            _settle_close(equity_strategy.exit, rest, position.equity, risk, ledger, "technical_swing_equity", underlying, reason,
                          {"broken_level": position.broken_level, "entered_at": position.entered_at, "signal_reason": position.signal_reason},
                          journal, "swing_trades", "TECH SWING EQUITY", lambda e, x, q: _equity_cost(cfg, e, x, q))
            del equity_positions[underlying]
        except Exception as e:
            log.exception("Failed swing equity exit check for %s - MANUAL INTERVENTION MAY BE NEEDED", underlying)
            notify_error(f"Failed swing equity exit check for {underlying} - MANUAL INTERVENTION MAY BE NEEDED - {e}")
    state_mod.save_technical_swing_equity(equity_positions)

    if not cfg.enable_trading:
        return
    if not risk.can_enter_new_trade():
        if not risk.cap_notified_today:
            notify(f"TECH SWING daily loss cap reached - no new swing entries today. Capital Rs.{ledger['current_capital']:.2f}")
            risk.cap_notified_today = True
        return

    for underlying in cfg.swing_watchlist_indices:
        if underlying in option_positions:
            continue
        try:
            _maybe_swing_index_enter(cfg, rest, instruments, strategy, risk, option_positions, underlying, today, journal)
        except Exception as e:
            log.exception("TECH SWING entry failed for %s", underlying)
            notify_error(f"TECH SWING entry failed for {underlying} - {e}")
    state_mod.save_technical_swing_option(option_positions)

    if not cfg.swing_equity_enabled:
        # "lets do only index options" (2026-09-09 evening) - stops NEW
        # swing-equity entries only; any equity positions already open when
        # this flipped keep being monitored/exited normally above, not
        # force-closed.
        return
    if len(equity_positions) >= cfg.swing_max_equity_positions:
        log.info("TECH SWING EQUITY book full (%d/%d) - skipping today's stock scan", len(equity_positions), cfg.swing_max_equity_positions)
        return
    try:
        universe_rows = build_universe(rest, instruments.instruments, cfg.swing_min_volume)
    except Exception as e:
        log.exception("Could not build TECH SWING EQUITY universe")
        notify_error(f"Could not build TECH SWING EQUITY universe - {e}")
        return
    log.info("TECH SWING EQUITY: scanning %d F&O-eligible/volume-filtered names", len(universe_rows))
    for row_index, row in enumerate(universe_rows):
        if row["name"] in equity_positions or len(equity_positions) >= cfg.swing_max_equity_positions:
            continue
        if row_index > 0:
            time.sleep(CANDLE_CALL_SLEEP_SECONDS)
        try:
            _maybe_swing_equity_enter(cfg, rest, equity_strategy, risk, equity_positions, row, today, journal)
        except Exception as e:
            log.exception("TECH SWING EQUITY entry failed for %s", row.get("name"))
            notify_error(f"TECH SWING EQUITY entry failed for {row.get('name')} - {e}")
    state_mod.save_technical_swing_equity(equity_positions)


# --- main -------------------------------------------------------------


def _new_journal(today: dt.date, cfg: TechnicalConfig, ledger: dict) -> dict:
    return {
        "date": today.isoformat(), "started_at": now_ist().isoformat(), "starting_capital": ledger["current_capital"],
        "scalp_decisions": [], "intraday_decisions": [], "swing_decisions": [],
        "scalp_trades": [], "intraday_trades": [], "swing_trades": [],
    }


_TIER_LABELS = {
    "technical_scalp": "SCALP",
    "technical_intraday": "INTRADAY",
    "technical_swing_option": "SWING (option)",
    "technical_swing_equity": "SWING (equity)",
}


def _tier_label(strategy_tag: str) -> str:
    return _TIER_LABELS.get(strategy_tag, strategy_tag or "?")


def _send_eod_trade_summary(cfg: TechnicalConfig, ledger: dict, today: dt.date, journal: dict) -> None:
    """Deterministic end-of-day ledger (not an agent-generated summary) -
    one line per closed trade with tier, instrument, entry/exit time, P&L,
    real charges paid, and running capital, plus day totals.
    `journal["starting_capital"]` (set once per day in _new_journal) is
    today's capital before any of today's trades - the exact, already-
    tracked value, not back-computed."""
    trades = state_mod.load_technical_trades_today(today.isoformat())
    header = f"{_dry_run_badge(cfg.dry_run)}\U0001F4CA <b>TECH DAILY SUMMARY</b> {today.isoformat()}"
    if not trades:
        notify(f"{header}\nNo trades today.", html=True)
        return

    lines = [header]
    for t in trades:
        pnl = t.get("realized_pnl", 0.0)
        dot = "\U0001F7E2" if pnl >= 0 else "\U0001F534"
        lines.append(
            f"{dot} [{esc(_tier_label(t.get('strategy', '')))}] {esc(str(t.get('underlying', '?')))} "
            f"{_format_time(t.get('entered_at', ''))}→{_format_time(t.get('closed_at', ''))}  "
            f"P&amp;L Rs.{pnl:.2f} (charges Rs.{t.get('costs', 0.0):.2f})  Capital Rs.{t.get('capital_after', 0.0):.2f}"
        )

    total_net = sum(t.get("realized_pnl", 0.0) for t in trades)
    total_cost = sum(t.get("costs", 0.0) for t in trades)
    wins = sum(1 for t in trades if t.get("realized_pnl", 0.0) > 0)
    losses = sum(1 for t in trades if t.get("realized_pnl", 0.0) < 0)
    lines.append("")
    lines.append(f"<b>Totals:</b> {len(trades)} trades ({wins}W/{losses}L)")
    lines.append(f"Net P&amp;L: Rs.{total_net:.2f} | Charges paid: Rs.{total_cost:.2f}")
    lines.append(f"Capital: Rs.{journal['starting_capital']:.2f} → Rs.{ledger['current_capital']:.2f}")
    notify("\n".join(lines), html=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Technical-indicator runner: scalp/intraday/swing tiers off pure TA signals "
                     "(EMA/RSI/MACD/VWAP, support/resistance, volume), dry-run by default"
    )
    parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    cfg = TechnicalConfig.from_env()
    base_cfg = Config.from_env()  # shared SmartAPI credentials (same account/api_key as run_daily.py)
    auth_cfg = dataclasses.replace(base_cfg, dry_run=cfg.dry_run)
    ledger = state_mod.load_technical_capital(cfg.capital)
    status_line = (
        f"{'DRY RUN' if cfg.dry_run else 'LIVE'}, trading {'ENABLED' if cfg.enable_trading else 'DISABLED (kill switch)'}, "
        f"scalp={len(cfg.scalp_watchlist)} intraday={len(cfg.intraday_watchlist)} names, "
        f"entry={cfg.entry_time} exit={cfg.exit_time}, capital=Rs.{ledger['current_capital']:.2f}"
    )
    log.info("Starting technical-indicator runner: %s", status_line)

    try:
        session = Session(auth_cfg)
        session.login()
    except Exception as e:
        log.exception("Login failed")
        notify(f"Technical runner FAILED TO START (login error) - {status_line}")
        notify_error(f"Technical runner login failed - {status_line} - {e}")
        raise
    rest = RestClient(session)
    strategy = LongOptionStrategy(rest, limit_buffer_pct=cfg.limit_order_buffer_pct)
    equity_strategy = EquityDeliveryStrategy(rest, limit_buffer_pct=cfg.limit_order_buffer_pct)

    scalp_risk = DailyRiskTracker(risk_per_trade_pct=cfg.scalp_risk_per_trade_pct, daily_loss_cap_pct=cfg.scalp_daily_loss_cap_pct, ledger=ledger)
    intraday_risk = DailyRiskTracker(risk_per_trade_pct=cfg.intraday_risk_per_trade_pct, daily_loss_cap_pct=cfg.intraday_daily_loss_cap_pct, ledger=ledger)
    swing_risk = DailyRiskTracker(risk_per_trade_pct=cfg.swing_risk_per_trade_pct, daily_loss_cap_pct=cfg.swing_daily_loss_cap_pct, ledger=ledger)

    scalp_positions = state_mod.load_technical_scalp()
    intraday_positions = state_mod.load_technical_intraday()
    swing_option_positions = state_mod.load_technical_swing_option()
    swing_equity_positions = state_mod.load_technical_swing_equity()

    instruments = InstrumentLookup(base_cfg.scrip_master_url)
    instruments.load()

    today = today_ist()
    entry_time, exit_time = _parse_hhmm(cfg.entry_time), _parse_hhmm(cfg.exit_time)
    eod_summary_time = _parse_hhmm(cfg.eod_summary_time)
    scalp_trade_counts = state_mod.count_trades_today("technical_scalp", today.isoformat())
    intraday_trade_counts = state_mod.count_trades_today("technical_intraday", today.isoformat())
    scalp_candle_cache: dict = {}
    intraday_candle_cache: dict = {}
    pivots_cache: dict = {}
    swing_scan_done_today = False
    eod_summary_sent_today = False

    journal = _new_journal(today, cfg, ledger)

    notify(f"\U0001F7E2 <b>TECHNICAL RUNNER STARTED</b>\n{esc(status_line)}", html=True)

    stop = False

    def handle_stop(signum, frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)
    log.info("Running. Press Ctrl+C to stop (open positions, if any, stay tracked under .state/technical_*).")

    while not stop:
        now = now_ist()
        if now.date() != today:
            today = now.date()
            scalp_risk.reset_day()
            intraday_risk.reset_day()
            swing_risk.reset_day()
            scalp_trade_counts.clear()
            intraday_trade_counts.clear()
            scalp_candle_cache.clear()
            intraday_candle_cache.clear()
            pivots_cache.clear()
            swing_scan_done_today = False
            eod_summary_sent_today = False
            journal = _new_journal(today, cfg, ledger)
        now_t = now.time()

        # --- scalp exit checks: fixed rupee-per-lot stop/target on the
        # OPTION'S OWN premium (not the underlying) - see premium_stop_target's
        # docstring. No trailing for this tier - target is a hard take-profit.
        # scalp_max_hold_minutes only force-closes a LOSING/flat position -
        # a currently-profitable trade is allowed to keep running past it
        # (toward its target, or worst case to the day's exit_time backstop
        # below, same as intraday already does) rather than being cut off
        # purely by the clock while it still has a real chance to work. ---
        if scalp_positions:
            for underlying, position in list(scalp_positions.items()):
                leg = position.option
                try:
                    ltp, pnl = _leg_ltp_and_pnl(rest, leg)
                except Exception as e:
                    log.exception("Could not price TECH SCALP %s for exit check", underlying)
                    notify_error(f"Could not price TECH SCALP {underlying} for exit check - {e}")
                    continue
                entered_at = dt.datetime.fromisoformat(position.entered_at)
                if now - entered_at >= dt.timedelta(minutes=cfg.scalp_max_hold_minutes) and pnl <= 0:
                    reason = "scalp_time_exit"
                elif stop_breached("long", position.stop_price, ltp):
                    reason = "stop_loss"
                elif target_reached("long", position.target_price, ltp):
                    reason = "take_profit"
                elif now_t >= exit_time:
                    reason = "exit_time"
                else:
                    continue
                try:
                    _settle_close(strategy.exit, rest, leg, scalp_risk, ledger, "technical_scalp", underlying, reason,
                                  {"tier": "scalp", "expiry": position.expiry, "option_type": leg.tradingsymbol[-2:],
                                   "entered_at": position.entered_at, "signal_reason": position.signal_reason},
                                  journal, "scalp_trades", "TECH SCALP", lambda e, x, q: _option_cost(cfg, e, x, q))
                    del scalp_positions[underlying]
                except Exception as e:
                    log.exception("Failed to close TECH SCALP %s - MANUAL INTERVENTION MAY BE NEEDED", underlying)
                    notify(f"URGENT: failed to close TECH SCALP {underlying} - MANUAL INTERVENTION NEEDED")
                    notify_error(f"Failed to close TECH SCALP {underlying} - MANUAL INTERVENTION NEEDED - {e}")
            state_mod.save_technical_scalp(scalp_positions)

        # --- intraday exit checks: same fixed rupee-per-lot mechanism as scalp above ---
        if intraday_positions:
            for underlying, position in list(intraday_positions.items()):
                leg = position.option
                try:
                    ltp, _pnl = _leg_ltp_and_pnl(rest, leg)
                except Exception as e:
                    log.exception("Could not price TECH INTRADAY %s for exit check", underlying)
                    notify_error(f"Could not price TECH INTRADAY {underlying} for exit check - {e}")
                    continue
                if now_t >= exit_time:
                    reason = "exit_time"
                elif stop_breached("long", position.stop_price, ltp):
                    reason = "stop_loss"
                elif target_reached("long", position.target_price, ltp):
                    reason = "take_profit"
                else:
                    continue
                try:
                    _settle_close(strategy.exit, rest, leg, intraday_risk, ledger, "technical_intraday", underlying, reason,
                                  {"tier": "intraday", "expiry": position.expiry, "option_type": leg.tradingsymbol[-2:],
                                   "entered_at": position.entered_at, "signal_reason": position.signal_reason},
                                  journal, "intraday_trades", "TECH INTRADAY", lambda e, x, q: _option_cost(cfg, e, x, q))
                    del intraday_positions[underlying]
                except Exception as e:
                    log.exception("Failed to close TECH INTRADAY %s - MANUAL INTERVENTION MAY BE NEEDED", underlying)
                    notify(f"URGENT: failed to close TECH INTRADAY {underlying} - MANUAL INTERVENTION NEEDED")
                    notify_error(f"Failed to close TECH INTRADAY {underlying} - MANUAL INTERVENTION NEEDED - {e}")
            state_mod.save_technical_intraday(intraday_positions)

        # --- scalp/intraday entries ---
        if cfg.enable_trading and entry_time <= now_t < exit_time:
            if scalp_risk.can_enter_new_trade():
                for underlying in cfg.scalp_watchlist:
                    if underlying in scalp_positions or scalp_trade_counts.get(underlying, 0) >= cfg.scalp_max_trades_per_day:
                        continue
                    try:
                        _maybe_scalp_enter(cfg, rest, instruments, strategy, scalp_risk, scalp_positions, scalp_trade_counts,
                                            underlying, today, scalp_candle_cache, journal)
                    except Exception as e:
                        log.exception("TECH SCALP entry check failed for %s", underlying)
                        notify_error(f"TECH SCALP entry check failed for {underlying} - {e}")
                state_mod.save_technical_scalp(scalp_positions)
            elif not scalp_risk.cap_notified_today:
                notify(f"TECH SCALP daily loss cap reached - no new scalp entries today. Capital Rs.{ledger['current_capital']:.2f}")
                scalp_risk.cap_notified_today = True

            if intraday_risk.can_enter_new_trade():
                for underlying in cfg.intraday_watchlist:
                    if underlying in intraday_positions or intraday_trade_counts.get(underlying, 0) >= cfg.intraday_max_trades_per_day:
                        continue
                    try:
                        pivots = _get_pivots(pivots_cache, rest, instruments, underlying, today)
                        _maybe_intraday_enter(cfg, rest, instruments, strategy, intraday_risk, intraday_positions, intraday_trade_counts,
                                               underlying, today, intraday_candle_cache, pivots, journal)
                    except Exception as e:
                        log.exception("TECH INTRADAY entry check failed for %s", underlying)
                        notify_error(f"TECH INTRADAY entry check failed for {underlying} - {e}")
                state_mod.save_technical_intraday(intraday_positions)
            elif not intraday_risk.cap_notified_today:
                notify(f"TECH INTRADAY daily loss cap reached - no new intraday entries today. Capital Rs.{ledger['current_capital']:.2f}")
                intraday_risk.cap_notified_today = True

        # --- swing: full scan (exits + entries) once per day ---
        if not swing_scan_done_today and now_t >= entry_time:
            try:
                _run_swing_scan(cfg, rest, instruments, strategy, equity_strategy, swing_risk, ledger,
                                 swing_option_positions, swing_equity_positions, today, journal)
            except Exception as e:
                log.exception("TECH SWING daily scan failed")
                notify_error(f"TECH SWING daily scan failed - {e}")
            swing_scan_done_today = True

        # --- swing: stop/target/trailing check, every cycle (trend-reversal/
        # max-hold exits are checked once/day in _run_swing_scan above) ---
        if swing_option_positions:
            for underlying, position in list(swing_option_positions.items()):
                try:
                    spot = _current_spot(rest, instruments, underlying)
                except Exception as e:
                    log.exception("Could not price TECH SWING %s for stop check", underlying)
                    notify_error(f"Could not price TECH SWING {underlying} for stop check - {e}")
                    continue
                position.favorable_extreme, position.stop_price, position.trailing_active = update_trailing_stop(
                    position.direction, position.entry_index_price, spot, position.atr_value, position.favorable_extreme,
                    position.stop_price, position.trailing_active, cfg.atr_trail_activate_mult, cfg.atr_trail_mult,
                )
                if stop_breached(position.direction, position.stop_price, spot):
                    reason = "trailing_stop_loss" if position.trailing_active else "stop_loss"
                elif target_reached(position.direction, position.target_price, spot):
                    reason = "take_profit"
                else:
                    continue
                try:
                    _settle_close(strategy.exit, rest, position.option, swing_risk, ledger, "technical_swing_option", underlying, reason,
                                  {"expiry": position.expiry, "option_type": position.option.tradingsymbol[-2:], "direction": position.direction,
                                   "broken_level": position.broken_level, "entered_at": position.entered_at, "signal_reason": position.signal_reason},
                                  journal, "swing_trades", "TECH SWING", lambda e, x, q: _option_cost(cfg, e, x, q))
                    del swing_option_positions[underlying]
                except Exception as e:
                    log.exception("Failed to close TECH SWING %s - MANUAL INTERVENTION MAY BE NEEDED", underlying)
                    notify(f"URGENT: failed to close TECH SWING {underlying} - MANUAL INTERVENTION NEEDED")
                    notify_error(f"Failed to close TECH SWING {underlying} - MANUAL INTERVENTION NEEDED - {e}")
            state_mod.save_technical_swing_option(swing_option_positions)

        if swing_equity_positions:
            for underlying, position in list(swing_equity_positions.items()):
                leg = position.equity
                try:
                    ltp, _pnl = _leg_ltp_and_pnl(rest, leg)
                except Exception as e:
                    log.exception("Could not price TECH SWING EQUITY %s for stop check", underlying)
                    notify_error(f"Could not price TECH SWING EQUITY {underlying} for stop check - {e}")
                    continue
                position.favorable_extreme, position.stop_price, position.trailing_active = update_trailing_stop(
                    "long", leg.entry_price, ltp, position.atr_value, position.favorable_extreme,
                    position.stop_price, position.trailing_active, cfg.atr_trail_activate_mult, cfg.atr_trail_mult,
                )
                if stop_breached("long", position.stop_price, ltp):
                    reason = "trailing_stop_loss" if position.trailing_active else "stop_loss"
                elif target_reached("long", position.target_price, ltp):
                    reason = "take_profit"
                else:
                    continue
                try:
                    _settle_close(equity_strategy.exit, rest, leg, swing_risk, ledger, "technical_swing_equity", underlying, reason,
                                  {"broken_level": position.broken_level, "entered_at": position.entered_at, "signal_reason": position.signal_reason},
                                  journal, "swing_trades", "TECH SWING EQUITY", lambda e, x, q: _equity_cost(cfg, e, x, q))
                    del swing_equity_positions[underlying]
                except Exception as e:
                    log.exception("Failed to close TECH SWING EQUITY %s - MANUAL INTERVENTION MAY BE NEEDED", underlying)
                    notify(f"URGENT: failed to close TECH SWING EQUITY {underlying} - MANUAL INTERVENTION NEEDED")
                    notify_error(f"Failed to close TECH SWING EQUITY {underlying} - MANUAL INTERVENTION NEEDED - {e}")
            state_mod.save_technical_swing_equity(swing_equity_positions)

        # --- end-of-day trade summary, once per day, 15 min past exit_time
        # so every same-day exit's trade_log write has landed ---
        if not eod_summary_sent_today and now_t >= eod_summary_time:
            try:
                _send_eod_trade_summary(cfg, ledger, today, journal)
            except Exception as e:
                log.exception("TECH EOD trade summary failed")
                notify_error(f"TECH EOD trade summary failed - {e}")
            eod_summary_sent_today = True

        time.sleep(POLL_SECONDS)

    log.info("Shutting down. Open positions (if any) remain tracked under .state/technical_* - rerun to keep managing them.")
    journal["ended_at"] = now_ist().isoformat()
    journal["ending_capital"] = ledger["current_capital"]
    state_mod.log_journal_day(journal)

    notify(
        f"\U0001F534 <b>TECHNICAL RUNNER STOPPED</b>\n"
        f"Capital Rs.{ledger['current_capital']:.2f}, "
        f"{len(scalp_positions)} scalp / {len(intraday_positions)} intraday / "
        f"{len(swing_option_positions) + len(swing_equity_positions)} swing position(s) still tracked.",
        html=True,
    )
    session.logout()


if __name__ == "__main__":
    main()
