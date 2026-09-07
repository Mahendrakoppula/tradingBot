import json
import logging
import threading
from typing import Callable

import websocket

from trading_bot.auth import Session

log = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_SECONDS = 10

ORDER_STATUS = {
    "AB00": "connected",
    "AB01": "open",
    "AB02": "cancelled",
    "AB03": "rejected",
    "AB04": "modified",
    "AB05": "complete",
    "AB06": "amo_received",
    "AB07": "amo_cancelled",
    "AB08": "amo_modify_received",
    "AB09": "open_pending",
    "AB10": "trigger_pending",
    "AB11": "modify_pending",
}


class OrderStatusStream:
    """Client for the WebSocket Order Status feed (JSON, replaces polling
    the order book for status changes on orders placed via the API).
    """

    def __init__(self, session: Session, on_update: Callable[[dict], None]):
        self.session = session
        self.on_update = on_update
        self._ws: websocket.WebSocketApp | None = None
        self._thread: threading.Thread | None = None
        self._heartbeat_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._connected = threading.Event()

    def connect(self, timeout: float = 10.0) -> None:
        headers = [f"Authorization: Bearer {self.session.jwt_token}"]
        self._ws = websocket.WebSocketApp(
            self.session.cfg.ws_order_url,
            header=headers,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self._thread = threading.Thread(target=self._ws.run_forever, daemon=True)
        self._thread.start()
        if not self._connected.wait(timeout):
            raise TimeoutError("Timed out connecting to order status WebSocket")

    def close(self) -> None:
        self._stop.set()
        if self._ws is not None:
            self._ws.close()

    def _on_open(self, ws) -> None:
        log.info("Order status WebSocket connected")
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
        if message == "pong":
            return
        update = json.loads(message)
        self.on_update(update)

    def _on_error(self, ws, error) -> None:
        log.error("Order status WebSocket error: %s", error)

    def _on_close(self, ws, close_status_code, close_msg) -> None:
        log.info("Order status WebSocket closed: %s %s", close_status_code, close_msg)
        self._connected.clear()
