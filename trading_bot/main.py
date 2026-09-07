import argparse
import logging
import signal
import time

from trading_bot.auth import Session
from trading_bot.config import Config
from trading_bot.instruments import InstrumentLookup
from trading_bot.rest_client import RestClient
from trading_bot.ws_market import EXCH_SEG_TO_WS_TYPE, MODE_LTP, MarketDataStream, Tick
from trading_bot.ws_orders import ORDER_STATUS, OrderStatusStream

log = logging.getLogger("trading_bot")


def on_tick(tick: Tick) -> None:
    log.info("TICK token=%s ltp=%.2f", tick.token, tick.ltp / 100)


def on_order_update(update: dict) -> None:
    status = ORDER_STATUS.get(update.get("order-status"), update.get("order-status"))
    log.info("ORDER UPDATE status=%s data=%s", status, update.get("orderData"))


def main() -> None:
    parser = argparse.ArgumentParser(description="SmartAPI trading bot skeleton (paper/dry-run by default)")
    parser.add_argument("--exchange", default="NSE", help="exch_seg to watch (scrip master values: NSE, BSE, NFO, BFO, MCX)")
    parser.add_argument("--symbol", default="SBIN-EQ", help="tradingsymbol to watch")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    cfg = Config.from_env()
    log.info("Starting in %s mode", "DRY RUN" if cfg.dry_run else "LIVE")

    session = Session(cfg)
    session.login()
    rest = RestClient(session)

    profile = rest.get_profile()
    log.info("Profile: %s", profile.get("name"))

    instruments = InstrumentLookup(cfg.scrip_master_url)
    instruments.load()
    instrument = instruments.find(args.exchange, args.symbol)
    log.info("Watching %s (token=%s)", args.symbol, instrument["token"])

    market_stream = MarketDataStream(session, on_tick=on_tick)
    market_stream.connect()
    ws_exchange_type = EXCH_SEG_TO_WS_TYPE[args.exchange]
    market_stream.subscribe(mode=MODE_LTP, exchange_type=ws_exchange_type, tokens=[instrument["token"]])

    order_stream = OrderStatusStream(session, on_update=on_order_update)
    order_stream.connect()

    stop = False

    def handle_sigint(signum, frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, handle_sigint)
    log.info("Running. Press Ctrl+C to stop.")
    while not stop:
        time.sleep(1)

    log.info("Shutting down...")
    market_stream.close()
    order_stream.close()
    session.logout()


if __name__ == "__main__":
    main()
