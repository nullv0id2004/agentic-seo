"""One global token bucket per external API, shared across projects.

PageSpeed and DataForSEO quotas are per key, not per site, so the limiter is keyed on the API name
and lives at process scope. The worker is a single process (Azure App Service, one instance); if that
ever changes, move the bucket to Postgres advisory locks.

Fails closed: when a bucket cannot grant a token inside `max_wait` seconds it raises RateLimited
rather than proceeding.
"""
from __future__ import annotations

import threading
import time

from config.settings import get_settings


class RateLimited(RuntimeError):
    pass


class TokenBucket:
    def __init__(self, per_minute: int, burst: int | None = None):
        self.rate = per_minute / 60.0
        self.capacity = burst or max(1, per_minute // 6)
        self.tokens = float(self.capacity)
        self.updated = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self, max_wait: float = 60.0) -> None:
        deadline = time.monotonic() + max_wait
        while True:
            with self.lock:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                wait = (1 - self.tokens) / self.rate
            if time.monotonic() + wait > deadline:
                raise RateLimited("rate limit wait would exceed max_wait; failing closed")
            time.sleep(min(wait, 1.0))


_buckets: dict[str, TokenBucket] = {}
_registry_lock = threading.Lock()


def bucket(api: str) -> TokenBucket:
    with _registry_lock:
        if api not in _buckets:
            per_minute = get_settings().rate_limits_per_minute.get(api, 30)
            _buckets[api] = TokenBucket(per_minute)
        return _buckets[api]


def acquire(api: str, max_wait: float = 60.0) -> None:
    bucket(api).acquire(max_wait)
