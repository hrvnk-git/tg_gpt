from __future__ import annotations

from redis.asyncio import Redis


class RateLimiter:
    def __init__(self, redis: Redis, limit: int, window_seconds: int) -> None:
        self._redis = redis
        self._limit = limit
        self._window = window_seconds

    async def allow(self, user_id: int) -> bool:
        key = f"rate-limit:{user_id}"
        count = await self._redis.incr(key)
        if count == 1:
            await self._redis.expire(key, self._window)
        return count <= self._limit

    async def time_to_reset(self, user_id: int) -> int:
        ttl = await self._redis.ttl(f"rate-limit:{user_id}")
        return max(ttl, 0)
