import time
import uuid
from collections import defaultdict, deque
from functools import lru_cache
from typing import Protocol

from redis.asyncio import Redis

from mabeyn.core.config import get_settings


class RateLimiter(Protocol):
    async def allow(self, key: str, limit: int, window_seconds: int) -> bool: ...


class MemoryRateLimiter:
    """Sliding window inside one process; used by tests and single-process setups."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    async def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and hits[0] <= now - window_seconds:
            hits.popleft()
        if len(hits) >= limit:
            return False
        hits.append(now)
        return True


class RedisRateLimiter:
    """Sliding window shared by every API process."""

    def __init__(self, client: Redis) -> None:
        self.client = client

    async def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.time()
        redis_key = f"ratelimit:{key}"
        async with self.client.pipeline(transaction=True) as pipe:
            pipe.zremrangebyscore(redis_key, 0, now - window_seconds)
            pipe.zcard(redis_key)
            _, count = await pipe.execute()
            if count >= limit:
                return False
            pipe.zadd(redis_key, {uuid.uuid4().hex: now})
            pipe.expire(redis_key, window_seconds)
            await pipe.execute()
        return True


@lru_cache
def get_rate_limiter() -> RateLimiter:
    return RedisRateLimiter(Redis.from_url(get_settings().redis_url))
