"""Client-side pacing for REST calls (SmartAPI historical: 3 req/s,
150/min, 5000/day - docs/smartapi-reference.md) plus a bounded retry that
knows which failures are worth retrying.

`paced_call` is the only way warmup talks to the broker, so a burst of
18 warmup requests never trips the per-second limit, and a transient
gateway error (HTTP 5xx / plain-text 403 from the CDN / timeout) is retried
with backoff instead of taking the bot down at 08:00.
"""
import logging
import time
from collections import deque
from typing import Callable, TypeVar

from trading_bot.rest_client import ApiError

log = logging.getLogger(__name__)
T = TypeVar("T")

# SmartAPI error codes worth a retry (throttling / transient), NOT session
# errors (rest_client handles those with a re-login) and NOT bad requests.
RETRYABLE_CODES = {"AB1004", "AB2001", "AB4001"}  # rate limit / something went wrong / gateway


class RateLimiter:
    """Sliding-window limiter: at most `rate_per_s` calls in any 1 s window
    and at most `per_minute` in any 60 s window. `wait()` blocks (via
    `sleep`) until a call is allowed and records it."""

    def __init__(self, rate_per_s: float = 2.5, per_minute: int = 140,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.rate_per_s = rate_per_s
        self.per_minute = per_minute
        self._clock = clock
        self._sleep = sleep
        self._recent: deque[float] = deque()

    def wait(self) -> float:
        """Returns seconds slept."""
        slept = 0.0
        while True:
            now = self._clock()
            while self._recent and now - self._recent[0] > 60.0:
                self._recent.popleft()
            last_sec = [t for t in self._recent if now - t < 1.0]
            delay = 0.0
            if len(last_sec) >= self.rate_per_s:
                delay = max(delay, 1.0 - (now - last_sec[0]) + 1e-3)
            if len(self._recent) >= self.per_minute:
                delay = max(delay, 60.0 - (now - self._recent[0]) + 1e-3)
            if delay <= 0:
                self._recent.append(now)
                return slept
            self._sleep(delay)
            slept += delay


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, ApiError):
        # HTTP_* codes come from rest_client's non-JSON handling: 5xx and the
        # CDN's plain-text 403 "Access denied" are transient; 429 is throttling
        return (exc.errorcode in RETRYABLE_CODES or exc.errorcode.startswith("HTTP_5")
                or exc.errorcode in ("HTTP_403", "HTTP_429"))
    # requests' ConnectionError / Timeout / JSONDecodeError all derive from these
    return isinstance(exc, (ConnectionError, TimeoutError, OSError, ValueError))


def paced_call(fn: Callable[..., T], *args, limiter: RateLimiter | None = None, retries: int = 3,
               backoff: float = 1.0, sleep: Callable[[float], None] = time.sleep, **kwargs) -> T:
    """Call `fn(*args, **kwargs)` under the limiter; retry transient
    failures up to `retries` times with exponential backoff. Non-retryable
    errors propagate immediately."""
    attempt = 0
    while True:
        if limiter is not None:
            limiter.wait()
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - classified below
            if not is_retryable(exc) or attempt >= retries:
                raise
            delay = backoff * (2 ** attempt)
            log.warning("transient error on %s (attempt %d/%d): %s - retrying in %.1fs",
                        getattr(fn, "__name__", fn), attempt + 1, retries, exc, delay)
            sleep(delay)
            attempt += 1
