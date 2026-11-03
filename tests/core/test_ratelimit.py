import uuid

import pytest
from redis.asyncio import Redis
from redis.exceptions import RedisError

from mabeyn.core.config import get_settings
from mabeyn.core.ratelimit import MemoryRateLimiter, RedisRateLimiter


async def test_the_memory_limiter_allows_up_to_the_limit_per_key():
    limiter = MemoryRateLimiter()
    assert [await limiter.allow("a", 2, 60) for _ in range(3)] == [True, True, False]
    assert await limiter.allow("b", 2, 60)


async def test_the_memory_limiter_forgets_old_hits(monkeypatch):
    import mabeyn.core.ratelimit as module

    now = [1000.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    limiter = MemoryRateLimiter()
    assert await limiter.allow("a", 1, 10)
    assert not await limiter.allow("a", 1, 10)
    now[0] += 11
    assert await limiter.allow("a", 1, 10)


async def test_the_redis_limiter_shares_its_window_between_processes():
    client = Redis.from_url(get_settings().redis_url)
    try:
        await client.ping()
    except (OSError, RedisError):
        await client.aclose()
        pytest.skip("redis is not available")
    key = f"test:{uuid.uuid4().hex}"
    first, second = (
        RedisRateLimiter(client),
        RedisRateLimiter(Redis.from_url(get_settings().redis_url)),
    )
    assert await first.allow(key, 2, 30)
    assert await second.allow(key, 2, 30)
    assert not await first.allow(key, 2, 30)
    await client.delete(f"ratelimit:{key}")
    await client.aclose()
    await second.client.aclose()
