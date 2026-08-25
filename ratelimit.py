"""
Simple thread-safe token-bucket rate limiter.

Why this exists: each external API this bot talks to has its own limit, and
a busy scan pass (BSC gets a steady stream of new pool launches) can easily
generate more calls per minute than these free tiers allow:

  - GeckoTerminal (no key):  ~30 requests/minute (undocumented but widely
                              observed; we stay under it deliberately)
  - BscScan (free key):      5 requests/second
  - honeypot.is (no key):    undocumented, so we're conservative: ~1 req/sec

Without throttling, a scan pass either silently drops real tokens when a
call gets rate-limited (bad: you miss a real signal and never know it), or
retries blindly and makes the rate-limiting worse. This limiter blocks
(sleeps) the calling thread so calls queue up in order instead of failing.

It's intentionally simple: no external deps, no async complexity. The API
clients are synchronous (httpx.Client), so a blocking sleep here is the
correct tool, not a workaround.
"""
import threading
import time


class RateLimiter:
    def __init__(self, max_calls: int, period_seconds: float):
        self.max_calls = max_calls
        self.period_seconds = period_seconds
        self._lock = threading.Lock()
        self._calls: list[float] = []

    def acquire(self):
        """Blocks until a call is allowed under the configured rate."""
        with self._lock:
            now = time.monotonic()
            self._calls = [t for t in self._calls if now - t < self.period_seconds]

            if len(self._calls) >= self.max_calls:
                sleep_for = self.period_seconds - (now - self._calls[0])
                if sleep_for > 0:
                    time.sleep(sleep_for)
                now = time.monotonic()
                self._calls = [t for t in self._calls if now - t < self.period_seconds]

            self._calls.append(time.monotonic())
