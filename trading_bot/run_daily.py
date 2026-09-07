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
from trading_bot.instruments import InstrumentLookup
from trading_bot.market_context import get_oi_buildup
from trading_bot.notifier import notify
from trading_bot.options import OptionChain, find_spot_instrument
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
                   ledger: dict, position: state_mod.OpenLongOption, reason: str) -> None:
    pnl = _unrealized_pnl(rest, position)
    strategy.exit(position.option)
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
    log.info("%s closed (%s): P&L %.2f, capital now Rs.%.2f", position.underlying, reason, pnl, ledger["current_capital"])
    notify(f"{'[DRY RUN] ' if rest.session.cfg.dry_run else ''}{position.underlying} {position.option.tradingsymbol} "
           f"closed ({reason}): P&L Rs.{pnl:.2f}, capital now Rs.{ledger['current_capital']:.2f}")


def _maybe_enter(cfg: Config, rest: RestClient, instruments: InstrumentLookup, strategy: LongOptionStrategy,
                  risk: DailyRiskTracker, oi_buildup: dict, positions: dict, underlying: str, today: dt.date) -> None:
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
        return
    log.info("%s: %s", underlying, reason)

    contract = build_long_leg(chain, expiry, spot, option_type, cfg.otm_distance_pct)
    log.info("%s expiry=%s spot=%.2f contract=%s", underlying, expiry, spot, contract.tradingsymbol)

    # Budget = the smaller of "max acceptable loss on one trade" and "max
    # capital fraction per trade" - for a long option these are the same
    # thing (premium paid = max loss), so this doubles as position sizing.
    budget = min(risk.max_loss_per_trade(), risk.ledger["current_capital"] * cfg.max_capital_pct_per_trade)
    lots, premium_per_lot = size_long_option(rest, contract, budget, cfg.max_lots_per_trade)
    if lots < 1:
        log.info("%s: budget Rs.%.2f can't cover 1 lot (premium Rs.%.2f) - skipping", underlying, budget, premium_per_lot)
        return

    leg = strategy.enter(contract, qty_lots=lots)
    positions[underlying] = state_mod.OpenLongOption(
        underlying=underlying,
        expiry=expiry.strftime("%d%b%Y").upper(),
        entered_at=now_ist().isoformat(),
        option=leg,
    )
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
    except Exception:
        log.exception("Login failed")
        notify(f"Daily runner FAILED TO START (login error) - {status_line}")
        raise
    rest = RestClient(session)
    strategy = LongOptionStrategy(rest)
    risk = DailyRiskTracker(risk_per_trade_pct=cfg.risk_per_trade_pct, daily_loss_cap_pct=cfg.daily_loss_cap_pct, ledger=ledger)

    instruments = InstrumentLookup(cfg.scrip_master_url)
    instruments.load()

    positions = state_mod.load_long()
    today = today_ist()
    entry_time, exit_time = _parse_hhmm(cfg.entry_time), _parse_hhmm(cfg.exit_time)

    notify(f"Daily long-option runner started - {status_line}")
    try:
        notify(build_morning_briefing(rest, instruments.instruments))
    except Exception:
        log.exception("Could not build morning briefing - continuing without it")

    stop = False

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

        if now_t >= exit_time:
            for underlying, position in list(positions.items()):
                log.info("Exit time reached - closing %s %s", underlying, position.option.tradingsymbol)
                try:
                    _settle_close(rest, strategy, risk, ledger, position, reason="exit_time")
                    del positions[underlying]
                except Exception:
                    log.exception("Failed to close %s at exit time - MANUAL INTERVENTION MAY BE NEEDED", underlying)
                    notify(f"URGENT: failed to close {underlying} at exit time - MANUAL INTERVENTION NEEDED")
            state_mod.save_long(positions)
        elif positions:
            for underlying, position in list(positions.items()):
                try:
                    pnl = _unrealized_pnl(rest, position)
                except Exception:
                    log.exception("Could not price %s for stop check", underlying)
                    continue
                if risk.should_exit_for_stop(pnl):
                    log.warning("%s hit per-trade stop (unrealized %.2f) - closing early", underlying, pnl)
                    try:
                        _settle_close(rest, strategy, risk, ledger, position, reason="stop_loss")
                        del positions[underlying]
                    except Exception:
                        log.exception("Failed to close %s on stop - MANUAL INTERVENTION MAY BE NEEDED", underlying)
                        notify(f"URGENT: failed to close {underlying} on stop-loss - MANUAL INTERVENTION NEEDED")
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
                            _maybe_enter(cfg, rest, instruments, strategy, risk, oi_buildup, positions, underlying, today)
                        except Exception:
                            log.exception("Entry failed for %s", underlying)
                            notify(f"Entry failed for {underlying}: check logs")
                    state_mod.save_long(positions)
            elif not risk.cap_notified_today:
                notify(f"Daily loss cap reached - no new entries today. Capital Rs.{ledger['current_capital']:.2f}")
                risk.cap_notified_today = True

        time.sleep(POLL_SECONDS)

    log.info("Shutting down. Open positions (if any) remain tracked in %s - rerun to keep managing them.", state_mod.LONG_STATE_PATH)
    notify(f"Daily runner shutting down. Capital Rs.{ledger['current_capital']:.2f}, {len(positions)} position(s) still tracked.")
    session.logout()


if __name__ == "__main__":
    main()
