"""Slušný HTTP klient: rate limit + retry s exponenciálním backoffem.

Cíl: nikdy nezahltit cizí server a ustát výpadky – bez obcházení ochran.
"""
from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

T = TypeVar("T")

RETRY_STATUS = {429, 500, 502, 503, 504}


class RateLimiter:
    """Token bucket – max `rate` požadavků za sekundu."""

    def __init__(self, rate_per_sec: float, burst: int = 1) -> None:
        self.rate = rate_per_sec
        self.capacity = burst
        self.tokens = float(burst)
        self.updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await asyncio.sleep((1 - self.tokens) / self.rate)


class RetryableError(Exception):
    pass


async def with_retry(
    fn: Callable[[], Awaitable[T]],
    attempts: int = 4,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
) -> T:
    last: Exception | None = None
    for i in range(attempts):
        try:
            return await fn()
        except (httpx.TransportError, RetryableError) as exc:
            last = exc
            if i == attempts - 1:
                break
            delay = min(max_delay, base_delay * 2**i) * (0.5 + random.random() / 2)
            await asyncio.sleep(delay)
    assert last is not None
    raise last


def raise_for_retry(resp: httpx.Response) -> None:
    if resp.status_code in RETRY_STATUS:
        raise RetryableError(f"HTTP {resp.status_code}")
    resp.raise_for_status()
