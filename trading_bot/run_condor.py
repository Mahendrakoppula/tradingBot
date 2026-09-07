import argparse
import datetime as dt
import logging
import signal
import time

from trading_bot import state as state_mod
from trading_bot.auth import Session
from trading_bot.config import Config
from trading_bot.instruments import InstrumentLookup
from trading_bot.market_filter import TradeFilter
from trading_bot.notifier import notify
from trading_bot.options import OptionChain, find_spot_instrument
from trading_bot.rest_client import RestClient
from trading_bot.risk import DailyRiskTracker
from trading_bot.sizing import size_condor
from trading_bot.strategy import IronCondorStrategy, build_iron_condor
from trading_bot.timeutil import now_ist, today_ist

log = logging.getLogger("trading_bot.condor")

POLL_SECONDS = 30


def _parse_hhmm(hhmm: str) -> dt.time:
    return dt.datetime.strptime(hhmm, "%H:%M").time()


def _condor_unrealized_pnl(rest: RestClient, condor: state_mod.OpenCondor) -> float:
    total = 0.0
    for leg in (condor.short_call, condor.short_put, condor.hedge_call, condor.hedge_put):
        data = rest.get_ltp(leg.exchange, leg.tradingsymbol, leg.symboltoken)
        ltp = float(data["ltp"])
        direction = 1 if leg.transaction_type == "SELL" else -1
        total += direction * (leg.entry_price - ltp) * leg.quantity
    return total


def _close_condor(strategy: IronCondorStrategy, condor: state_mod.OpenCondor) -> None:
    strategy.exit(
        {
            "short_call": condor.short_call,
            "short_put": condor.short_put,
            "hedge_call": condor.hedge_call,
            "hedge_put": condor.hedge_put,
        }
    )


def _settle_close(rest: RestClient, strategy: IronCondorStrategy, risk: DailyRiskTracker,
                   ledger: dict, condor: state_mod.OpenCondor, reason: str) -> None:
    """Closes a condor, updates the capital ledger + daily P&L, and appends
    a trade-log record. Raises on failure so the caller decides whether to
    drop it from tracked positions (only on success)."""
    pnl = _condor_unrealized_pnl(rest, condor)
    _close_condor(strategy, condor)
    risk.record_realized(pnl)
    ledger["current_capital"] += pnl
    ledger["updated_at"] = now_ist().isoformat()
    state_mod.save_capital(ledger)
    state_mod.log_trade({
        "underlying": condor.underlying,
        "expiry": condor.expiry,
        "entered_at": condor.entered_at,
        "closed_at": ledger["updated_at"],
        "reason": reason,
        "qty_lots": condor.short_call.quantity // condor.short_call.lotsize,
        "realized_pnl": pnl,
        "capital_after": ledger["current_capital"],
    })
    log.info("%s closed (%s): P&L %.2f, capital now Rs.%.2f", condor.underlying, reason, pnl, ledger["current_capital"])
    notify(f"{'[DRY RUN] ' if rest.session.cfg.dry_run else ''}{condor.underlying} closed ({reason}): "
           f"P&L Rs.{pnl:.2f}, capital now Rs.{ledger['current_capital']:.2f}")


def _maybe_enter(cfg: Config, rest: RestClient, instruments: InstrumentLookup, strategy: IronCondorStrategy,
                  risk: DailyRiskTracker, trade_filter: TradeFilter, positions: dict, underlying: str,
                  today: dt.date) -> None:
    chain = OptionChain(instruments.instruments, underlying, exchange="NFO")
    expiry = chain.nearest_expiry_within(today, cfg.dte_min, cfg.dte_max)
    if expiry is None:
        log.info("%s: no expiry within DTE window [%d, %d]", underlying, cfg.dte_min, cfg.dte_max)
        return

    ok, reason = trade_filter.should_trade(rest, underlying)
    if not ok:
        log.info("%s: skipping entry - %s", underlying, reason)
        return
    log.info("%s: entry filter passed - %s", underlying, reason)

    spot_row = find_spot_instrument(instruments.instruments, underlying)
    spot_data = rest.get_ltp(spot_row["exch_seg"], spot_row["symbol"], spot_row["token"])
    spot = float(spot_data["ltp"])

    legs = build_iron_condor(chain, expiry, spot, cfg.otm_distance_pct, cfg.wing_distance_pct)
    log.info(
        "%s expiry=%s spot=%.2f short_call=%s short_put=%s hedge_call=%s hedge_put=%s",
        underlying, expiry, spot,
        legs.short_call.tradingsymbol, legs.short_put.tradingsymbol,
        legs.hedge_call.tradingsymbol, legs.hedge_put.tradingsymbol,
    )

    # Margin sizing caps exposure per trade to a fraction of current capital;
    # the daily loss cap (risk.can_enter_new_trade) is a separate check the
    # caller already made before getting here.
    budget = risk.ledger["current_capital"] * cfg.max_capital_pct_per_trade
    lots, margin_per_lot = size_condor(rest, legs, budget, cfg.max_lots_per_trade)
    if lots < 1:
        log.info("%s: budget Rs.%.2f can't cover 1 lot (margin Rs.%.2f) - skipping", underlying, budget, margin_per_lot)
        return

    fills = strategy.enter(legs, qty_lots=lots)
    positions[underlying] = state_mod.OpenCondor(
        underlying=underlying,
        expiry=expiry.strftime("%d%b%Y").upper(),
        entered_at=now_ist().isoformat(),
        short_call=fills["short_call"],
        short_put=fills["short_put"],
        hedge_call=fills["hedge_call"],
        hedge_put=fills["hedge_put"],
    )
    notify(
        f"{'[DRY RUN] ' if cfg.dry_run else ''}Entered {underlying} condor: {lots} lot(s), expiry {expiry}, "
        f"short {legs.short_call.tradingsymbol}/{legs.short_put.tradingsymbol}, "
        f"hedge {legs.hedge_call.tradingsymbol}/{legs.hedge_put.tradingsymbol}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Near-expiry iron condor runner: NIFTY/BANKNIFTY/stock options, same-day only, dry-run by default"
    )
    parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    cfg = Config.from_env()
    ledger = state_mod.load_capital(cfg.capital)
    status_line = (
        f"{'DRY RUN' if cfg.dry_run else 'LIVE'}, trading {'ENABLED' if cfg.enable_trading else 'DISABLED (kill switch)'}, "
        f"watchlist={cfg.watchlist}, DTE=[{cfg.dte_min},{cfg.dte_max}], entry={cfg.entry_time} exit={cfg.exit_time}, "
        f"capital=Rs.{ledger['current_capital']:.2f}"
    )
    log.info("Starting condor runner: %s", status_line)

    try:
        session = Session(cfg)
        session.login()
    except Exception:
        log.exception("Login failed")
        notify(f"Condor runner FAILED TO START (login error) - {status_line}")
        raise
    rest = RestClient(session)
    strategy = IronCondorStrategy(rest)
    risk = DailyRiskTracker(risk_per_trade_pct=cfg.risk_per_trade_pct, daily_loss_cap_pct=cfg.daily_loss_cap_pct, ledger=ledger)
    trade_filter = TradeFilter(vix_min=cfg.vix_min, vix_max=cfg.vix_max, pcr_min=cfg.pcr_min, pcr_max=cfg.pcr_max)

    instruments = InstrumentLookup(cfg.scrip_master_url)
    instruments.load()

    positions = state_mod.load()
    today = today_ist()
    entry_time, exit_time = _parse_hhmm(cfg.entry_time), _parse_hhmm(cfg.exit_time)

    notify(f"Condor runner started - {status_line}")

    stop = False

    def handle_stop(signum, frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)  # systemd/EC2 stop sends SIGTERM, not SIGINT
    log.info("Running. Press Ctrl+C to stop (open positions, if any, stay tracked in %s).", state_mod.STATE_PATH)

    while not stop:
        now = now_ist()
        if now.date() != today:
            today = now.date()
            risk.reset_day()
        now_t = now.time()

        if now_t >= exit_time:
            for underlying, condor in list(positions.items()):
                log.info("Exit time reached - closing %s condor", underlying)
                try:
                    _settle_close(rest, strategy, risk, ledger, condor, reason="exit_time")
                    del positions[underlying]
                except Exception:
                    log.exception("Failed to close %s at exit time - MANUAL INTERVENTION MAY BE NEEDED", underlying)
                    notify(f"URGENT: failed to close {underlying} at exit time - MANUAL INTERVENTION NEEDED")
            state_mod.save(positions)
        elif positions:
            for underlying, condor in list(positions.items()):
                try:
                    pnl = _condor_unrealized_pnl(rest, condor)
                except Exception:
                    log.exception("Could not price %s for stop check", underlying)
                    continue
                if risk.should_exit_for_stop(pnl):
                    log.warning("%s hit per-trade stop (unrealized %.2f) - closing early", underlying, pnl)
                    try:
                        _settle_close(rest, strategy, risk, ledger, condor, reason="stop_loss")
                        del positions[underlying]
                    except Exception:
                        log.exception("Failed to close %s on stop - MANUAL INTERVENTION MAY BE NEEDED", underlying)
                        notify(f"URGENT: failed to close {underlying} on stop-loss - MANUAL INTERVENTION NEEDED")
            state_mod.save(positions)

        if cfg.enable_trading and entry_time <= now_t < exit_time:
            if risk.can_enter_new_trade():
                for underlying in cfg.watchlist:
                    if underlying in positions:
                        continue
                    try:
                        _maybe_enter(cfg, rest, instruments, strategy, risk, trade_filter, positions, underlying, today)
                    except Exception:
                        log.exception("Entry failed for %s", underlying)
                        notify(f"Entry failed for {underlying}: check logs")
                state_mod.save(positions)
            elif not risk.cap_notified_today:
                notify(f"Daily loss cap reached - no new entries today. Capital Rs.{ledger['current_capital']:.2f}")
                risk.cap_notified_today = True

        time.sleep(POLL_SECONDS)

    log.info("Shutting down. Open positions (if any) remain tracked in %s - rerun to keep managing them.", state_mod.STATE_PATH)
    notify(f"Condor runner shutting down. Capital Rs.{ledger['current_capital']:.2f}, {len(positions)} position(s) still tracked.")
    session.logout()


if __name__ == "__main__":
    main()
