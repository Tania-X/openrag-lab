"""Minimal in-process rate limiter for auth endpoints.

This is a defense-in-depth baseline for single-process deployments. It is
not a replacement for a shared rate limiter (Redis, API gateway) in a
multi-worker or multi-instance production deployment.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from collections.abc import Callable

from fastapi import HTTPException, Request, status

_buckets: dict[str, deque[float]] = defaultdict(deque)
_lock = asyncio.Lock()


def rate_limit(limit: int, window_seconds: float) -> Callable:
    """Return a FastAPI dependency that enforces ``limit`` per window."""

    async def dependency(request: Request) -> None:
        client = request.client.host if request.client else "unknown"
        key = f"{client}:{request.url.path}"
        now = time.monotonic()
        async with _lock:
            bucket = _buckets[key]
            while bucket and now - bucket[0] > window_seconds:
                bucket.popleft()
            if len(bucket) >= limit:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="rate_limit_exceeded",
                )
            bucket.append(now)

    return dependency
