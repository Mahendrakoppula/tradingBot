"""Resilient market-data feed (spec §1): wraps `ws_market.MarketDataStream`
with a supervisor that reconnects on close/error with capped exponential
backoff, re-subscribes, and restarts the heartbeat (the raw client's
heartbeat thread dies on its first send failure and never comes back).

Threading contract: the WS library calls `on_tick` on ITS thread; here it
does nothing but `queue.put_nowait(tick)` and stamp the health record. All
analysis runs on the main thread, which drains the queue. A full queue
drops the tick and counts it - back-pressure must never block the socket
thread or the broker disconnects us.

`stream_factory(on_tick) -> object with connect()/subscribe()/close()` is
injectable so tests can script connects, drops and ticks without sockets.
"""
import datetime as dt
import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable

from trading_bot.engine.quality import FeedHealth
from trading_bot.timeutil import IST, now_ist

log = logging.getLogger(__name__)

DEFAULT_BACKOFF = (1, 2, 4, 8, 16, 32, 60)


@dataclass(frozen=True)
class Subscription:
    exchange_type: str  # "nse_cm" | "nse_fo" | "bse_cm" | "bse_fo"
    tokens: tuple[str, ...]
    mode: int = 2  # MODE_QUOTE


class ResilientMarketStream:
    def __init__(self, stream_factory: Callable[[Callable], object], subscriptions: list[Subscription],
                 out_queue: "queue.Queue", backoff: tuple[int, ...] = DEFAULT_BACKOFF, backoff_max: int = 60,
                 clock: Callable[[], dt.datetime] = now_ist, sleep: Callable[[float], None] = time.sleep,
                 connect_timeout: float = 10.0):
        self._factory = stream_factory
        self._subs = list(subscriptions)
        self._q = out_queue
        self._backoff = tuple(min(b, backoff_max) for b in backoff)
        self._clock = clock
        self._sleep = sleep
        self._connect_timeout = connect_timeout
        self._stream = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._down = threading.Event()  # set by on_close/on_error, cleared after reconnect
        self._supervisor: threading.Thread | None = None
        # health
        self.connected = False
        self.last_tick_at: dt.datetime | None = None
        self.last_exchange_ts_ms: int | None = None
        self.reconnects = 0
        self.dropped = 0
        self.ticks = 0
        self.last_error: str | None = None
        self.connected_since: dt.datetime | None = None

    # --- lifecycle -------------------------------------------------------------

    def start(self) -> None:
        """Connect (raises if the FIRST connect fails - startup must be
        loud) then supervise in the background."""
        self._connect_and_subscribe()
        self._supervisor = threading.Thread(target=self._supervise, name="feed-supervisor", daemon=True)
        self._supervisor.start()

    def close(self) -> None:
        self._stop.set()
        self._down.set()  # wake the supervisor so it can exit
        with self._lock:
            if self._stream is not None:
                try:
                    self._stream.close()
                except Exception as exc:  # noqa: BLE001
                    log.debug("stream close: %s", exc)
            self.connected = False

    def health(self) -> FeedHealth:
        drift = None
        if self.last_tick_at is not None and self.last_exchange_ts_ms is not None:
            drift = (self.last_tick_at - dt.datetime.fromtimestamp(self.last_exchange_ts_ms / 1000, tz=IST)).total_seconds()
        return FeedHealth(connected=self.connected, last_tick_at=self.last_tick_at, reconnects=self.reconnects,
                          last_error=self.last_error, clock_drift_seconds=drift)

    # --- callbacks from the socket thread ------------------------------------------

    def _on_tick(self, tick) -> None:
        self.last_tick_at = self._clock()
        self.last_exchange_ts_ms = getattr(tick, "exchange_timestamp", None)
        self.ticks += 1
        try:
            self._q.put_nowait(tick)
        except queue.Full:
            self.dropped += 1

    def on_disconnect(self, reason: str) -> None:
        """Called by the stream (or by the loop when it detects silence) to
        request a reconnect."""
        self.last_error = reason
        self.connected = False
        self._down.set()

    # --- internals --------------------------------------------------------------------

    def _connect_and_subscribe(self) -> None:
        with self._lock:
            if self._stream is not None:
                try:
                    self._stream.close()
                except Exception:  # noqa: BLE001
                    pass
            stream = self._factory(self._on_tick)
            if hasattr(stream, "on_disconnect"):
                stream.on_disconnect = self.on_disconnect
            stream.connect(timeout=self._connect_timeout)
            for s in self._subs:
                stream.subscribe(mode=s.mode, exchange_type=s.exchange_type, tokens=list(s.tokens))
            self._stream = stream
            self.connected = True
            self.connected_since = self._clock()
            self._down.clear()

    def _supervise(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            self._down.wait()
            if self._stop.is_set():
                return
            delay = self._backoff[min(attempt, len(self._backoff) - 1)]
            log.warning("feed down (%s) - reconnecting in %ss (attempt %d)", self.last_error, delay, attempt + 1)
            self._sleep(delay)
            if self._stop.is_set():
                return
            try:
                self._connect_and_subscribe()
                self.reconnects += 1
                attempt = 0
                log.info("feed reconnected (%d total)", self.reconnects)
            except Exception as exc:  # noqa: BLE001 - keep trying until stopped
                self.last_error = f"reconnect failed: {exc}"
                attempt += 1


def smartapi_stream_factory(session):
    """The production factory: wraps ws_market.MarketDataStream and routes
    its on_close/on_error into the supervisor via `on_disconnect`."""
    from trading_bot import ws_market

    def make(on_tick):
        stream = ws_market.MarketDataStream(session, on_tick=on_tick)
        stream.on_disconnect = None  # set by ResilientMarketStream

        orig_close, orig_error = stream._on_close, stream._on_error

        def _on_close(ws, code, msg):
            orig_close(ws, code, msg)
            if stream.on_disconnect and not stream._stop.is_set():
                stream.on_disconnect(f"closed {code} {msg}")

        def _on_error(ws, error):
            orig_error(ws, error)
            if stream.on_disconnect and not stream._stop.is_set():
                stream.on_disconnect(f"error {error}")

        stream._on_close, stream._on_error = _on_close, _on_error
        return stream

    return make


class LiveTickSource:
    """ShadowLoop's TickSource over a ResilientMarketStream: ticks are
    pulled off the queue on the MAIN thread; `done()` is never True live -
    the loop ends on the session clock."""

    def __init__(self, stream: ResilientMarketStream, q: "queue.Queue"):
        self.stream = stream
        self.q = q

    def start(self) -> None:
        self.stream.start()

    def next(self, timeout: float = 1.0):
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None

    def done(self) -> bool:
        return False

    def health(self) -> FeedHealth:
        return self.stream.health()

    def close(self) -> None:
        self.stream.close()
