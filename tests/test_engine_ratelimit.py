from trading_bot.engine.ratelimit import RateLimiter, is_retryable, paced_call
from trading_bot.rest_client import ApiError, RestClient


class _Clock:
    def __init__(self):
        self.t = 1000.0
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def test_limiter_enforces_per_second_window():
    clk = _Clock()
    lim = RateLimiter(rate_per_s=2, per_minute=100, clock=clk.now, sleep=clk.sleep)
    assert lim.wait() == 0 and lim.wait() == 0
    slept = lim.wait()  # third call within the same second must wait ~1s
    assert 0.99 <= slept <= 1.01 and clk.slept


def test_limiter_enforces_per_minute_window():
    clk = _Clock()
    lim = RateLimiter(rate_per_s=100, per_minute=3, clock=clk.now, sleep=clk.sleep)
    for _ in range(3):
        lim.wait()
        clk.t += 0.5
    slept = lim.wait()
    assert slept > 55


def test_paced_call_retries_transient_then_succeeds():
    calls = []
    slept = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise ApiError("Access denied", "HTTP_403")
        return "ok"

    assert paced_call(flaky, retries=3, backoff=0.5, sleep=slept.append) == "ok"
    assert len(calls) == 3 and slept == [0.5, 1.0]


def test_paced_call_does_not_retry_non_transient():
    calls = []

    def bad():
        calls.append(1)
        raise ApiError("Invalid token", "AB1001")

    try:
        paced_call(bad, retries=3, sleep=lambda s: None)
        assert False
    except ApiError:
        pass
    assert len(calls) == 1


def test_paced_call_gives_up_after_retries():
    calls = []

    def down():
        calls.append(1)
        raise ConnectionError("boom")

    try:
        paced_call(down, retries=2, sleep=lambda s: None)
        assert False
    except ConnectionError:
        pass
    assert len(calls) == 3


def test_is_retryable_classification():
    assert is_retryable(ApiError("x", "HTTP_502")) and is_retryable(ApiError("x", "HTTP_429"))
    assert is_retryable(ApiError("x", "AB1004")) and is_retryable(TimeoutError())
    assert not is_retryable(ApiError("x", "AB1010")) and not is_retryable(ApiError("x", "HTTP_400"))
    assert not is_retryable(KeyError("x"))


# --- rest_client non-JSON handling -----------------------------------------------------


class _Resp:
    def __init__(self, status, text, payload=None):
        self.status_code, self.text, self._payload = status, text, payload

    def json(self):
        if self._payload is None:
            raise ValueError("No JSON")
        return self._payload


class _Session:
    class cfg:
        root_url = "https://x"

    def headers(self, authenticated=True):
        return {}


def test_rest_client_wraps_plain_text_403(monkeypatch):
    import trading_bot.rest_client as rc
    monkeypatch.setattr(rc.requests, "request", lambda *a, **k: _Resp(403, "Access denied"))
    client = RestClient(_Session())
    try:
        client._call_once("GET", "/p")
        assert False
    except ApiError as e:
        assert e.errorcode == "HTTP_403" and "Access denied" in str(e)
        assert is_retryable(e)


def test_rest_client_wraps_non_dict_payload(monkeypatch):
    import trading_bot.rest_client as rc
    monkeypatch.setattr(rc.requests, "request", lambda *a, **k: _Resp(502, "", payload=["not", "a", "dict"]))
    try:
        RestClient(_Session())._call_once("GET", "/p")
        assert False
    except ApiError as e:
        assert e.errorcode == "HTTP_502"


def test_defaults_stay_under_the_shared_account_budget():
    """Angel's 3/s, 150/min is per ACCOUNT and the daily bot shares it."""
    lim = RateLimiter()
    assert lim.rate_per_s <= 1.5 and lim.per_minute <= 100
    slept = []
    calls = {"n": 0}

    def throttled():
        calls["n"] += 1
        if calls["n"] <= 5:
            raise ApiError("Access denied because of exceeding access rate", "HTTP_403")
        return "ok"

    assert paced_call(throttled, sleep=slept.append) == "ok"
    assert sum(slept) >= 60  # default retries ride out a full minute window
