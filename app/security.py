from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque

from fastapi import Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import IdempotencyRow, utcnow


def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    settings = get_settings()
    if not x_api_key or x_api_key not in settings.api_key_set:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def check_ws_token(token: str | None) -> None:
    settings = get_settings()
    if not token or token not in settings.api_key_set:
        raise HTTPException(status_code=401, detail="Invalid or missing token")


class RateLimiter:
    """In-memory sliding window. For multi-instance deployments replace with Redis."""

    def __init__(self, max_calls: int, period: float = 60.0) -> None:
        self.max_calls = max_calls
        self.period = period
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def check(self, key: str) -> None:
        async with self._lock:
            now = time.monotonic()
            events = self._events[key]
            while events and events[0] <= now - self.period:
                events.popleft()
            if len(events) >= self.max_calls:
                raise HTTPException(status_code=429, detail="Rate limit exceeded")
            events.append(now)


_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter(max_calls=get_settings().rate_limit_per_minute)
    return _limiter


async def idempotent_response(
    db: AsyncSession, key: str | None, endpoint: str
) -> dict | None:
    if not key:
        return None
    result = await session_get(db, key, endpoint)
    return result


async def session_get(db: AsyncSession, key: str, endpoint: str) -> dict | None:
    row = await db.scalar(
        select(IdempotencyRow).where(
            IdempotencyRow.key == key, IdempotencyRow.endpoint == endpoint
        )
    )
    return row.response if row else None


async def store_idempotent(
    db: AsyncSession, key: str | None, endpoint: str, response: dict
) -> None:
    if not key:
        return
    existing = await session_get(db, key, endpoint)
    if existing is not None:
        return
    db.add(
        IdempotencyRow(key=key, endpoint=endpoint, response=response, created_at=utcnow())
    )
    await db.commit()
