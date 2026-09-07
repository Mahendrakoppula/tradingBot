import json
import logging
import struct
import threading
import time
from dataclasses import dataclass
from typing import Callable

import websocket

from trading_bot.auth import Session

log = logging.getLogger(__name__)

MODE_LTP = 1
MODE_QUOTE = 2
MODE_SNAPQUOTE = 3

EXCHANGE_TYPE = {
    "nse_cm": 1,
    "nse_fo": 2,
    "bse_cm": 3,
    "bse_fo": 4,
    "mcx_fo": 5,
}

# Maps the scrip master's `exch_seg` field (e.g. "NSE", "NFO") to the WebSocket
# subscription mnemonics above. These are two different namings for the same
# exchanges - verified against a live OpenAPIScripMaster.json dump, whose
# exch_seg values are plain codes, not the nse_cm/nse_fo style used here.
EXCH_SEG_TO_WS_TYPE = {
    "NSE": "nse_cm",
    "NFO": "nse_fo",
    "BSE": "bse_cm",
    "BFO": "bse_fo",
    "MCX": "mcx_fo",
}

HEARTBEAT_INTERVAL_SECONDS = 30


@dataclass
class DepthLevel:
    quantity: int
    price: int
    orders: int


@dataclass
class Tick:
    mode: int
    exchange_type: int
    token: str
    sequence_number: int
    exchange_timestamp: int
    ltp: int
    last_traded_qty: int | None = None
    avg_traded_price: int | None = None
    volume: int | None = None
    total_buy_qty: float | None = None
    total_sell_qty: float | None = None
    open: int | None = None
    high: int | None = None
    low: int | None = None
    close: int | None = None
    last_traded_timestamp: int | None = None
    open_interest: int | None = None
    best_five_buy: list[DepthLevel] | None = None
    best_five_sell: list[DepthLevel] | None = None
    upper_circuit: int | None = None
    lower_circuit: int | None = None
    week52_high: int | None = None
    week52_low: int | None = None


def parse_tick(data: bytes) -> Tick:
    mode, exchange_type = struct.unpack_from("<bb", data, 0)
    token = data[2:27].split(b"\x00", 1)[0].decode("utf-8")
    (sequence_number,) = struct.unpack_from("<q", data, 27)
    (exchange_timestamp,) = struct.unpack_from("<q", data, 35)
    (ltp,) = struct.unpack_from("<q", data, 43)

    tick = Tick(
        mode=mode,
        exchange_type=exchange_type,
        token=token,
        sequence_number=sequence_number,
        exchange_timestamp=exchange_timestamp,
        ltp=ltp,
    )
    if mode == MODE_LTP or len(data) < 123:
        return tick

    (tick.last_traded_qty,) = struct.unpack_from("<q", data, 51)
    (tick.avg_traded_price,) = struct.unpack_from("<q", data, 59)
    (tick.volume,) = struct.unpack_from("<q", data, 67)
    (tick.total_buy_qty,) = struct.unpack_from("<d", data, 75)
    (tick.total_sell_qty,) = struct.unpack_from("<d", data, 83)
    (tick.open,) = struct.unpack_from("<q", data, 91)
    (tick.high,) = struct.unpack_from("<q", data, 99)
    (tick.low,) = struct.unpack_from("<q", data, 107)
    (tick.close,) = struct.unpack_from("<q", data, 115)

    if mode == MODE_QUOTE or len(data) < 379:
        return tick

    (tick.last_traded_timestamp,) = struct.unpack_from("<q", data, 123)
    (tick.open_interest,) = struct.unpack_from("<q", data, 131)

    levels = []
    for i in range(10):
        offset = 147 + i * 20
        flag, qty, price, orders = struct.unpack_from("<hqqh", data, offset)
        levels.append((flag, DepthLevel(quantity=qty, price=price, orders=orders)))
    tick.best_five_buy = [level for flag, level in levels if flag == 1]
    tick.best_five_sell = [level for flag, level in levels if flag == 0]

    (tick.upper_circuit,) = struct.unpack_from("<q", data, 347)
    (tick.lower_circuit,) = struct.unpack_from("<q", data, 355)
    (tick.week52_high,) = struct.unpack_from("<q", data, 363)
    (tick.week52_low,) = struct.unpack_from("<q", data, 371)
    return tick


class MarketDataStream:
    """Client for WebSocket Streaming 2.0 (binary market data feed).

    Usage:
        stream = MarketDataStream(session, on_tick=my_handler)
        stream.connect()
        stream.subscribe(mode=MODE_LTP, exchange_type="nse_cm", tokens=["3045"])
        ...
        stream.close()
    """

    def __init__(self, session: Session, on_tick: Callable[[Tick], None]):
        self.session = session
        self.on_tick = on_tick
        self._ws: websocket.WebSocketApp | None = None
        self._thread: threading.Thread | None = None
        self._heartbeat_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._connected = threading.Event()

    def connect(self, timeout: float = 10.0) -> None:
        headers = [
            f"Authorization: Bearer {self.session.jwt_token}",
            f"x-api-key: {self.session.cfg.api_key}",
            f"x-client-code: {self.session.cfg.client_code}",
            f"x-feed-token: {self.session.feed_token}",
        ]
        self._ws = websocket.WebSocketApp(
            self.session.cfg.ws_market_url,
            header=headers,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self._thread = threading.Thread(target=self._ws.run_forever, daemon=True)
        self._thread.start()
        if not self._connected.wait(timeout):
            raise TimeoutError("Timed out connecting to market data WebSocket")

    def subscribe(self, mode: int, exchange_type: str, tokens: list[str], correlation_id: str = "sub1") -> None:
        self._send_request(action=1, mode=mode, exchange_type=exchange_type, tokens=tokens, correlation_id=correlation_id)

    def unsubscribe(self, mode: int, exchange_type: str, tokens: list[str], correlation_id: str = "unsub1") -> None:
        self._send_request(action=0, mode=mode, exchange_type=exchange_type, tokens=tokens, correlation_id=correlation_id)

    def close(self) -> None:
        self._stop.set()
        if self._ws is not None:
            self._ws.close()

    def _send_request(self, action: int, mode: int, exchange_type: str, tokens: list[str], correlation_id: str) -> None:
        payload = {
            "correlationID": correlation_id,
            "action": action,
            "params": {
                "mode": mode,
                "tokenList": [{"exchangeType": EXCHANGE_TYPE[exchange_type], "tokens": tokens}],
            },
        }
        self._ws.send(json.dumps(payload))

    def _on_open(self, ws) -> None:
        log.info("Market data WebSocket connected")
        self._connected.set()
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(HEARTBEAT_INTERVAL_SECONDS):
            try:
                self._ws.send("ping")
            except (OSError, websocket.WebSocketException) as e:
                log.warning("Heartbeat send failed: %s", e)
                return

    def _on_message(self, ws, message) -> None:
        if isinstance(message, str):
            if message == "pong":
                return
            log.warning("Unexpected text frame: %s", message)
            return
        try:
            tick = parse_tick(message)
        except struct.error as e:
            log.error("Failed to parse tick packet (%d bytes): %s", len(message), e)
            return
        self.on_tick(tick)

    def _on_error(self, ws, error) -> None:
        log.error("Market data WebSocket error: %s", error)

    def _on_close(self, ws, close_status_code, close_msg) -> None:
        log.info("Market data WebSocket closed: %s %s", close_status_code, close_msg)
        self._connected.clear()
