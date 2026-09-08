import argparse
import datetime as dt
import logging
import signal
import time

from trading_bot import state as state_mod
from trading_bot.auth import Session
from trading_bot.briefing import build_morning_briefing
from trading_bot.config import Config
from trading_bot.debit_strategy import LongOptionStrategy, build_long_leg, pick_direction, pick_momentum_direction
from trading_bot.error_notifier import notify_error
from trading_bot.instruments import InstrumentLookup
from trading_bot.liquidity import check_liquidity, get_quote_for_contract
from trading_bot.market_context import get_oi_buildup
from trading_bot.notifier import notify
from trading_bot.option_chain_logger import log_snapshot
from trading_bot.options import OptionChain, find_spot_instrument
from trading_bot.premarket_bias import allows_direction, compute_premarket_bias, format_bias_line
from trading_bot.rest_client import RestClient
from trading_bot.risk import DailyRiskTracker
from trading_bot.sizing import size_long_option
from trading_bot.timeutil import now_ist, today_ist

log = logging.getLogger("trading_bot.daily")

POLL_SECONDS = 30


def _parse_hhmm(hhmm: str) -> dt.time:
    return dt.datetime.strptime(hhmm, "%H:%M").time()


def _unrealized_pnl(rest: RestClient, position: state_mod.OpenLongOption) -> float:
    leg = position.option
    data = rest.get_ltp(leg.exchange, leg.tradingsymbol, leg.symboltoken)
    ltp = float(data["ltp"])
    return (ltp - leg.entry_price) * leg.quantity  # long option: profit if price rises


def _settle_close(rest: RestClient, strategy: LongOptionStrategy, risk: DailyRiskTracker,
                   ledger: dict, position: state_mod.OpenLongOption, reason: str, journal: dict) -> None:
    pnl = _unrealized_pnl(rest, position)
    leg = position.option
    try:
        exit_quote = get_quote_for_contract(rest, leg.exchange, leg.symboltoken)
    except Exception:
        log.exception("Could not fetch exit quote for %s - falling back to MARKET order", leg.tradingsymbol)
        exit_quote = None
    strategy.exit(leg, quote=exit_quote)
    risk.record_realized(pnl)
    ledger["current_capital"] += pnl
    ledger["updated_at"] = now_ist().isoformat()
    state_mod.save_capital(ledger)
    state_mod.log_trade({
        "underlying": position.underlying,
        "expiry": position.expiry,
        "option_type": position.option.tradingsymbol[-2:],
        "entered_at": position.entered_at,
        "closed_at": ledger["updated_at"],
        "reason": reason,
        "qty_lots": position.option.quantity // position.option.lotsize,
        "realized_pnl": pnl,
        "capital_after": ledger["current_capital"],
    })
    journal["trades"].append({"underlying": position.underlying, "pnl": pnl, "reason": reason})
    log.info("%s closed (%s): P&L %.2f, capital now Rs.%.2f", position.underlying, reason, pnl, ledger["current_capital"])
    notify(f"{'[DRY RUN] ' if rest.session.cfg.dry_run else ''}{position.underlying} {position.option.tradingsymbol} "
           f"closed ({reason}): P&L Rs.{pnl:.2f}, capital now Rs.{ledger['current_capital']:.2f}")


def _maybe_enter(cfg: Config, rest: RestClient, instruments: InstrumentLookup, strategy: LongOptionStrategy,
                  risk: DailyRiskTracker, oi_buildup: dict, bias: dict, positions: dict, underlying: str,
                  today: dt.date, journal: dict) -> None:
    chain = OptionChain(instruments.instruments, underlying, exchange="NFO")
    expiry = chain.nearest_expiry_within(today, cfg.dte_min, cfg.dte_max)
    if expiry is None:
        log.info("%s: no expiry within DTE window [%d, %d]", underlying, cfg.dte_min, cfg.dte_max)
        return

    spot_row = find_spot_instrument(instruments.instruments, underlying)
    spot_data = rest.get_ltp(spot_row["exch_seg"], spot_row["symbol"], spot_row["token"])
    spot = float(spot_data["ltp"])

    option_type, reason = pick_direction(oi_buildup, underlying)
    if option_type is None:
        # OI buildup never covers indices (verified live - stock futures only),
        # so this fallback is what actually gives NIFTY/BANKNIFTY a signal.
        option_type, reason = pick_momentum_direction(spot_data, cfg.momentum_min_move_pct)
    if option_type is None:
        log.info("%s: skipping entry - %s", underlying, reason)
        journal["decisions"].append({"time": now_ist().isoformat(), "underlying": underlying, "action": "skipped", "reason": reason})
        return

    if cfg.premarket_bias_gate_enabled and not allows_direction(bias, option_type):
        skip_reason = f"{option_type} signal ({reason}) conflicts with pre-market bias {bias['bias']}"
        log.info("%s: skipping entry - %s", underlying, skip_reason)
        journal["decisions"].append({"time": now_ist().isoformat(), "underlying": underlying, "action": "skipped", "reason": skip_reason})
        return

    log.info("%s: %s", underlying, reason)

    contract = build_long_leg(chain, expiry, spot, option_type, cfg.otm_distance_pct)
    log.info("%s expiry=%s spot=%.2f contract=%s", underlying, expiry, spot, contract.tradingsymbol)

    # Check the CONTRACT's own liquidity (not just the underlying's) before
    # doing anything else - a thin far-OTM strike can have a wide spread
    # even when the underlying itself trades fine.
    quote = get_quote_for_contract(rest, contract.exchange, contract.token)
    if quote is None:
        skip_reason = "could not fetch a quote for this contract"
        log.info("%s: skipping entry - %s", underlying, skip_reason)
        journal["decisions"].append({"time": now_ist().isoformat(), "underlying": underlying, "action": "skipped", "reason": skip_reason})
        return
    liquid, liquidity_reason = check_liquidity(quote, cfg.max_spread_pct, cfg.min_open_interest)
    if not liquid:
        skip_reason = f"{contract.tradingsymbol} illiquid - {liquidity_reason}"
        log.info("%s: skipping entry - %s", underlying, skip_reason)
        journal["decisions"].append({"time": now_ist().isoformat(), "underlying": underlying, "action": "skipped", "reason": skip_reason})
        return

    # Budget = the smaller of "max acceptable loss on one trade" and "max
    # capital fraction per trade" - for a long option these are the same
    # thing (premium paid = max loss), so this doubles as position sizing.
    budget = min(risk.max_loss_per_trade(), risk.ledger["current_capital"] * cfg.max_capital_pct_per_trade)
    lots, premium_per_lot = size_long_option(rest, contract, budget, cfg.max_lots_per_trade)
    if lots < 1:
        skip_reason = f"budget Rs.{budget:.2f} can't cover 1 lot (premium Rs.{premium_per_lot:.2f})"
        log.info("%s: skipping entry - %s", underlying, skip_reason)
        journal["decisions"].append({"time": now_ist().isoformat(), "underlying": underlying, "action": "skipped", "reason": skip_reason})
        return

    leg = strategy.enter(contract, qty_lots=lots, quote=quote)
    positions[underlying] = state_mod.OpenLongOption(
        underlying=underlying,
        expiry=expiry.strftime("%d%b%Y").upper(),
        entered_at=now_ist().isoformat(),
        option=leg,
    )
    journal["decisions"].append({
        "time": now_ist().isoformat(), "underlying": underlying, "action": "entered",
        "reason": reason, "option_type": option_type, "lots": lots,
    })
    notify(
        f"{'[DRY RUN] ' if cfg.dry_run else ''}Bought {underlying} {contract.tradingsymbol}: {lots} lot(s) "
        f"@ ~Rs.{leg.entry_price:.2f}, premium Rs.{premium_per_lot * lots:.2f} ({reason})"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Daily intraday long-option runner: buys NIFTY/BANKNIFTY/stock options based on an "
                     "OI-buildup direction signal, no margin needed, dry-run by default"
    )
    parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    cfg = Config.from_env()
    ledger = state_mod.load_capital(cfg.capital)
    status_line = (
        f"{'DRY RUN' if cfg.dry_run else 'LIVE'}, trading {'ENABLED' if cfg.enable_trading else 'DISABLED (kill switch)'}, "
        f"watchlist={cfg.watchlist}, entry={cfg.entry_time} exit={cfg.exit_time}, capital=Rs.{ledger['current_capital']:.2f}"
    )
    log.info("Starting daily long-option runner: %s", status_line)

    try:
        session = Session(cfg)
        session.login()
    except Exception as e:
        log.exception("Login failed")
        notify(f"Daily runner FAILED TO START (login error) - {status_line}")
        notify_error(f"Login failed - {status_line} - {e}")
        raise
    rest = RestClient(session)
    strategy = LongOptionStrategy(rest, limit_buffer_pct=cfg.limit_order_buffer_pct)

    # Losing-streak circuit breaker: bounded, safe automatic risk reduction -
    # NOT auto-tuning entry signals (overfitting risk on sparse data), just
    # shrinks size until a human reviews the journal and decides on a real
    # change. See config.py docstring.
    streak = ledger.get("losing_streak_days", 0)
    risk_pct, cap_pct = cfg.risk_per_trade_pct, cfg.daily_loss_cap_pct
    if streak >= cfg.losing_streak_cooldown_days:
        risk_pct *= cfg.losing_streak_risk_multiplier
        cap_pct *= cfg.losing_streak_risk_multiplier
        msg = f"Losing streak: {streak} consecutive losing days - risk reduced to {cfg.losing_streak_risk_multiplier:.0%} today"
        log.warning(msg)
        notify(msg)
    risk = DailyRiskTracker(risk_per_trade_pct=risk_pct, daily_loss_cap_pct=cap_pct, ledger=ledger)

    instruments = InstrumentLookup(cfg.scrip_master_url)
    instruments.load()

    positions = state_mod.load_long()
    today = today_ist()
    entry_time, exit_time = _parse_hhmm(cfg.entry_time), _parse_hhmm(cfg.exit_time)

    notify(f"Daily long-option runner started - {status_line}")
    try:
        notify(build_morning_briefing(rest, instruments.instruments))
    except Exception as e:
        log.exception("Could not build morning briefing - continuing without it")
        notify_error(f"Morning briefing failed to build - {e}")

    # Pre-market bias: genuinely LEADING (computed once, before the entry
    # window, from overnight US close + VIX + today's economic calendar) -
    # unlike the OI-buildup/momentum signal, which is inherently lagging.
    try:
        bias = compute_premarket_bias(rest, cfg.us_move_threshold_pct, cfg.vix_caution_level)
        notify(format_bias_line(bias))
    except Exception as e:
        log.exception("Could not compute pre-market bias - defaulting to NEUTRAL (no gating)")
        notify_error(f"Pre-market bias computation failed, defaulting to NEUTRAL - {e}")
        bias = {"bias": "NEUTRAL", "reasons": ["computation failed"], "us_overnight_pct": None, "vix": None, "economic_events_today": None}

    journal = {
        "date": today.isoformat(),
        "started_at": now_ist().isoformat(),
        "premarket_bias": bias,
        "watchlist": list(cfg.watchlist),
        "starting_capital": ledger["current_capital"],
        "losing_streak_days_at_start": streak,
        "decisions": [],
        "trades": [],
    }

    stop = False
    last_snapshot_at = 0.0  # time.monotonic() of last option-chain log, 0 = never yet

    def handle_stop(signum, frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)
    log.info("Running. Press Ctrl+C to stop (open positions, if any, stay tracked in %s).", state_mod.LONG_STATE_PATH)

    while not stop:
        now = now_ist()
        if now.date() != today:
            today = now.date()
            risk.reset_day()
        now_t = now.time()

        # Option-chain snapshot logging: pure data collection for a future
        # premium-based backtest (SmartAPI has no historical option data for
        # already-expired contracts - see research/README.md), never affects
        # trading decisions and runs regardless of ENABLE_TRADING/DRY_RUN.
        # Gated to regular market hours so it doesn't burn API calls before
        # the open or after the close.
        if (
            cfg.option_chain_log_enabled
            and dt.time(9, 15) <= now_t <= dt.time(15, 30)
            and time.monotonic() - last_snapshot_at >= cfg.option_chain_log_interval_seconds
        ):
            log_snapshot(rest, instruments, cfg.watchlist, cfg.dte_min, cfg.dte_max, today, cfg.option_chain_log_strike_band_pct)
            last_snapshot_at = time.monotonic()

        if now_t >= exit_time:
            for underlying, position in list(positions.items()):
                log.info("Exit time reached - closing %s %s", underlying, position.option.tradingsymbol)
                try:
                    _settle_close(rest, strategy, risk, ledger, position, reason="exit_time", journal=journal)
                    del positions[underlying]
                except Exception as e:
                    log.exception("Failed to close %s at exit time - MANUAL INTERVENTION MAY BE NEEDED", underlying)
                    notify(f"URGENT: failed to close {underlying} at exit time - MANUAL INTERVENTION NEEDED")
                    notify_error(f"Failed to close {underlying} at exit time - MANUAL INTERVENTION NEEDED - {e}")
            state_mod.save_long(positions)
        elif positions:
            for underlying, position in list(positions.items()):
                try:
                    pnl = _unrealized_pnl(rest, position)
                except Exception as e:
                    log.exception("Could not price %s for stop check", underlying)
                    notify_error(f"Could not price {underlying} for stop check - {e}")
                    continue
                if risk.should_exit_for_stop(pnl):
                    log.warning("%s hit per-trade stop (unrealized %.2f) - closing early", underlying, pnl)
                    try:
                        _settle_close(rest, strategy, risk, ledger, position, reason="stop_loss", journal=journal)
                        del positions[underlying]
                    except Exception as e:
                        log.exception("Failed to close %s on stop - MANUAL INTERVENTION MAY BE NEEDED", underlying)
                        notify(f"URGENT: failed to close {underlying} on stop-loss - MANUAL INTERVENTION NEEDED")
                        notify_error(f"Failed to close {underlying} on stop-loss - MANUAL INTERVENTION NEEDED - {e}")
            state_mod.save_long(positions)

        if cfg.enable_trading and entry_time <= now_t < exit_time:
            if risk.can_enter_new_trade():
                candidates = [u for u in cfg.watchlist if u not in positions]
                if candidates:
                    # Fetch once per cycle and reuse for every underlying below -
                    # this endpoint covers all symbols per call and is rate-limited
                    # to ~1 req/sec (confirmed live), so don't re-fetch per underlying.
                    oi_buildup = get_oi_buildup(rest)
                    for underlying in candidates:
                        try:
                            _maybe_enter(cfg, rest, instruments, strategy, risk, oi_buildup, bias, positions, underlying, today, journal)
                        except Exception as e:
                            log.exception("Entry failed for %s", underlying)
                            notify(f"Entry failed for {underlying}: check logs")
                            notify_error(f"Entry failed for {underlying} - {e}")
                    state_mod.save_long(positions)
            elif not risk.cap_notified_today:
                notify(f"Daily loss cap reached - no new entries today. Capital Rs.{ledger['current_capital']:.2f}")
                risk.cap_notified_today = True

        time.sleep(POLL_SECONDS)

    log.info("Shutting down. Open positions (if any) remain tracked in %s - rerun to keep managing them.", state_mod.LONG_STATE_PATH)

    # Update the losing-streak counter and write today's journal entry -
    # this is the raw material for periodic human review, not automatic
    # strategy rewriting.
    day_pnl = risk.daily_pnl()
    ledger["losing_streak_days"] = (streak + 1) if day_pnl < 0 else 0
    state_mod.save_capital(ledger)
    journal["ended_at"] = now_ist().isoformat()
    journal["ending_capital"] = ledger["current_capital"]
    journal["total_pnl"] = day_pnl
    journal["losing_streak_days_at_end"] = ledger["losing_streak_days"]
    state_mod.log_journal_day(journal)

    notify(f"Daily runner shutting down. Capital Rs.{ledger['current_capital']:.2f}, {len(positions)} position(s) still tracked.")
    session.logout()


if __name__ == "__main__":
    main()
