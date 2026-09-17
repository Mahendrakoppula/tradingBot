import datetime as dt
import queue
import threading

from trading_bot.engine.feed import ResilientMarketStream, Subscription
from trading_bot.timeutil import IST


class FakeStream:
    """Scripted stand-in for ws_market.MarketDataStream."""
    instances: list["FakeStream"] = []

    def __init__(self, on_tick, fail_connect=False):
        self.on_tick = on_tick
        self.fail_connect = fail_connect
        self.subscriptions = []
        self.closed = False
        self.on_disconnect = None
        FakeStream.instances.append(self)

    def connect(self, timeout=10.0):
        if self.fail_connect:
            raise TimeoutError("connect failed")

    def subscribe(self, mode, exchange_type, tokens, correlation_id="sub1"):
        self.subscriptions.append((mode, exchange_type, tuple(tokens)))

    def close(self):
        self.closed = True

    # test helpers
    def push(self, tick):
        self.on_tick(tick)

    def drop(self, reason="closed 1006 abnormal"):
        self.on_disconnect(reason)


class _Tick:
    def __init__(self, ms):
        self.exchange_timestamp = ms


SUBS = [Subscription("nse_cm", ("99926000", "99926009")), Subscription("nse_fo", ("12345",))]


def _make(fail_first=0, q=None):
    FakeStream.instances.clear()
    fails = {"n": fail_first}
    clk = {"t": dt.datetime(2026, 9, 16, 9, 15, tzinfo=IST)}
    slept = []

    def factory(on_tick):
        fails["n"] -= 1
        return FakeStream(on_tick, fail_connect=fails["n"] >= 0)

    def sleep(s):
        slept.append(s)
        clk["t"] += dt.timedelta(seconds=s)

    feed = ResilientMarketStream(factory, SUBS, q or queue.Queue(), backoff=(1, 2, 4), clock=lambda: clk["t"], sleep=sleep)
    return feed, clk, slept


def _wait_for(pred, timeout=2.0):
    ev = threading.Event()
    deadline = dt.datetime.now() + dt.timedelta(seconds=timeout)
    while dt.datetime.now() < deadline:
        if pred():
            return True
        ev.wait(0.01)
    return False


def test_start_connects_and_subscribes_everything():
    feed, clk, _ = _make()
    feed.start()
    try:
        s = FakeStream.instances[0]
        assert s.subscriptions == [(2, "nse_cm", ("99926000", "99926009")), (2, "nse_fo", ("12345",))]
        assert feed.health().connected and feed.health().last_tick_at is None
    finally:
        feed.close()
    assert s.closed


def test_ticks_go_to_queue_only_and_update_health():
    q = queue.Queue()
    feed, clk, _ = _make(q=q)
    feed.start()
    try:
        s = FakeStream.instances[0]
        ms = int(clk["t"].timestamp() * 1000) - 3000  # exchange stamp 3s behind wall clock
        s.push(_Tick(ms))
        assert q.get_nowait().exchange_timestamp == ms
        h = feed.health()
        assert h.last_tick_at == clk["t"] and abs(h.clock_drift_seconds - 3.0) < 1e-6 and feed.ticks == 1
    finally:
        feed.close()


def test_full_queue_drops_and_counts_instead_of_blocking():
    q = queue.Queue(maxsize=1)
    feed, clk, _ = _make(q=q)
    feed.start()
    try:
        s = FakeStream.instances[0]
        s.push(_Tick(1))
        s.push(_Tick(2))
        assert feed.dropped == 1 and q.qsize() == 1
    finally:
        feed.close()


def test_reconnects_with_backoff_and_resubscribes():
    feed, clk, slept = _make()
    feed.start()
    try:
        first = FakeStream.instances[0]
        first.drop("closed 1006")
        assert not feed.health().connected
        assert _wait_for(lambda: feed.reconnects == 1)
        assert slept == [1]
        second = FakeStream.instances[1]
        assert second is not first and first.closed
        assert second.subscriptions == first.subscriptions
        assert feed.health().connected and feed.health().last_error == "closed 1006"
        # a second drop backs off further; a successful reconnect resets the ladder
        second.drop("error EOF")
        assert _wait_for(lambda: feed.reconnects == 2)
        assert slept == [1, 1]
    finally:
        feed.close()


def test_failed_reconnect_attempts_climb_the_backoff_ladder():
    feed, clk, slept = _make()
    feed.start()
    try:
        # make the next two factory calls produce streams that fail to connect
        orig = feed._factory
        bad = {"n": 2}

        def factory(on_tick):
            bad["n"] -= 1
            return FakeStream(on_tick, fail_connect=bad["n"] >= 0)

        feed._factory = factory
        FakeStream.instances[0].drop("closed")
        assert _wait_for(lambda: feed.reconnects == 1, timeout=3)
        assert slept == [1, 2, 4]  # two failures then success, capped ladder
        assert "connect failed" in (feed.last_error or "") or feed.health().connected
        feed._factory = orig
    finally:
        feed.close()


def test_first_connect_failure_is_loud():
    feed, _, _ = _make(fail_first=1)
    try:
        feed.start()
        assert False
    except TimeoutError:
        pass
    assert not feed.health().connected
    feed.close()
