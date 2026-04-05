from __future__ import annotations

from typing import List

from redis.asyncio import Redis

from .config import Settings


class AccessControl:
    """
    Управляет allowlist'ами пользователей/админов через Redis, чтобы можно было
    добавлять/удалять доступ без перезапуска бота.
    """

    def __init__(self, redis: Redis, settings: Settings) -> None:
        self._redis = redis
        self._settings = settings

        self._allowed_key = "tg:allowed_users"
        self._admin_key = "tg:admin_users"

    async def seed_if_empty(self) -> None:
        if not self._settings.allow_all_users:
            if await self._redis.scard(self._allowed_key) == 0:
                if self._settings.allowed_user_ids:
                    await self._redis.sadd(
                        self._allowed_key, *[str(x) for x in self._settings.allowed_user_ids]
                    )

        if not self._settings.allow_all_admins:
            if await self._redis.scard(self._admin_key) == 0:
                if self._settings.admin_user_ids:
                    await self._redis.sadd(
                        self._admin_key, *[str(x) for x in self._settings.admin_user_ids]
                    )

    async def is_user_allowed(self, user_id: int) -> bool:
        if self._settings.allow_all_users:
            return True
        return bool(await self._redis.sismember(self._allowed_key, str(user_id)))

    async def is_admin(self, user_id: int) -> bool:
        if self._settings.allow_all_admins:
            return True
        return bool(await self._redis.sismember(self._admin_key, str(user_id)))

    async def list_allowed_users(self, limit: int = 200) -> List[int]:
        if self._settings.allow_all_users:
            return []
        members = await self._redis.smembers(self._allowed_key)
        # members могут быть bytes/str
        ids: List[int] = []
        for m in members:
            try:
                ids.append(int(m))
            except Exception:
                continue
        ids.sort()
        return ids[:limit]

    async def add_allowed_user(self, user_id: int) -> None:
        if self._settings.allow_all_users:
            return
        await self._redis.sadd(self._allowed_key, str(user_id))

    async def remove_allowed_user(self, user_id: int) -> None:
        if self._settings.allow_all_users:
            return
        await self._redis.srem(self._allowed_key, str(user_id))

