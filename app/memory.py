from __future__ import annotations

import asyncio
import logging
import json
import uuid
from typing import Any, Dict, List

from redis.asyncio import Redis

from .config import Settings


class ConversationMemory:
    def __init__(self, redis: Redis, settings: Settings) -> None:
        self._redis = redis
        self._settings = settings
        self._logger = logging.getLogger(__name__)

        # Guardrail to prevent concurrent read-modify-write overwrites for the same user.
        self._lock_ttl_ms = 5_000
        self._lock_retry_attempts = 40
        self._lock_retry_delay_s = 0.05

    def _user_lock_key(self, user_id: int) -> str:
        return f"lock:chat:{user_id}"

    async def _acquire_user_lock(self, user_id: int) -> tuple[bool, str]:
        """
        Acquire a per-user Redis lock.
        Returns: (acquired, token).
        """
        lock_key = self._user_lock_key(user_id)
        token = uuid.uuid4().hex

        for _ in range(self._lock_retry_attempts):
            # SET key value NX PX ttl_ms
            acquired = await self._redis.set(
                lock_key, token, nx=True, px=self._lock_ttl_ms
            )
            if acquired:
                return True, token
            await asyncio.sleep(self._lock_retry_delay_s)

        return False, token

    async def _release_user_lock(self, user_id: int, token: str) -> None:
        """
        Release the lock only if token matches (prevents deleting somebody else's lock).
        """
        lock_key = self._user_lock_key(user_id)
        lua = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""
        try:
            await self._redis.eval(lua, 1, lock_key, token)
        except Exception:
            # Lock cleanup must not crash the bot.
            self._logger.exception("Failed to release user lock for %s", user_id)

    async def _with_user_lock(self, user_id: int, fn):
        acquired, token = await self._acquire_user_lock(user_id)
        if not acquired:
            # Best-effort fallback to avoid downtime; may reintroduce races under heavy load.
            self._logger.warning("Could not acquire lock for user=%s; proceeding without lock", user_id)
            return await fn()

        try:
            return await fn()
        finally:
            await self._release_user_lock(user_id, token)

    def _key(self, user_id: int) -> str:
        return f"chat:{user_id}:history"

    def _summary_key(self, user_id: int) -> str:
        return f"chat:{user_id}:summary"

    async def append_and_get_stored_history(
        self, user_id: int, role: str, content: str
    ) -> List[Dict[str, Any]]:
        """
        Аппендим сообщение в "широкую" историю (с заделом под summary) и возвращаем
        итоговую trimmed историю (включая system элемент).
        """
        async def _work() -> List[Dict[str, Any]]:
            history = await self.get_stored_history(user_id)
            history.append({"role": role, "content": content})
            trimmed = self._trim_history(history)
            await self._redis.set(
                self._key(user_id),
                json.dumps(trimmed),
                ex=self._settings.redis_history_ttl,
            )
            return trimmed

        return await self._with_user_lock(user_id, _work)

    def build_history(
        self, stored_history: List[Dict[str, Any]], summary: str
    ) -> List[Dict[str, Any]]:
        """
        Собирает payload для модели:
        - system: базовый system_prompt + при наличии summary добавляет блок summary
        - messages: обрезка до history_max_messages (только user/assistant)
        """
        if not stored_history:
            stored_history = self._initial_history()

        stored_system = stored_history[0]
        messages = stored_history[1:]

        system_prompt = stored_system.get("content", self._settings.system_prompt)
        if summary.strip():
            system_prompt = (
                f"{self._settings.system_prompt}\n\nConversation summary:\n{summary}"
            )
        else:
            system_prompt = self._settings.system_prompt

        trimmed_messages = self._trim_messages(messages, self._settings.history_max_messages)
        return [{"role": "system", "content": system_prompt}, *trimmed_messages]

    async def get_history(self, user_id: int) -> List[Dict[str, Any]]:
        stored_history, summary = await asyncio.gather(
            self.get_stored_history(user_id),
            self.get_summary(user_id),
        )
        return self.build_history(stored_history, summary)

    async def get_stored_history(self, user_id: int) -> List[Dict[str, Any]]:
        key = self._key(user_id)
        data = await self._redis.get(key)
        if not data:
            return self._initial_history()
        try:
            history = json.loads(data)
        except json.JSONDecodeError:
            history = self._initial_history()
        return self._ensure_system_prompt(history)

    async def get_summary(self, user_id: int) -> str:
        data = await self._redis.get(self._summary_key(user_id))
        if not data:
            return ""
        return str(data)

    async def append(self, user_id: int, role: str, content: str) -> None:
        # append работает с хранимой (широкой) историей, чтобы у нас было что суммировать.
        async def _work() -> None:
            history = await self.get_stored_history(user_id)
            history.append({"role": role, "content": content})
            trimmed = self._trim_history(history)
            encoded = json.dumps(trimmed)
            await self._redis.set(
                self._key(user_id),
                encoded,
                ex=self._settings.redis_history_ttl,
            )

        await self._with_user_lock(user_id, _work)

    async def reset(self, user_id: int) -> None:
        async def _work() -> None:
            await self._redis.delete(self._key(user_id))
            await self._redis.delete(self._summary_key(user_id))

        await self._with_user_lock(user_id, _work)

    async def set_summary(self, user_id: int, summary: str) -> None:
        summary = summary.strip()
        if not summary:
            return
        async def _work() -> None:
            await self._redis.set(
                self._summary_key(user_id),
                summary[: self._settings.summary_max_chars],
                ex=self._settings.redis_history_ttl,
            )

        await self._with_user_lock(user_id, _work)

    async def set_recent_history(
        self,
        user_id: int,
        recent_messages: List[Dict[str, Any]] | None = None,
    ) -> List[Dict[str, Any]]:
        """
        Recompute "recent" from the latest stored history under a per-user lock to avoid
        clobbering messages that arrived while summary generation was running.
        Returns the recent slice that was persisted.
        """

        async def _work() -> List[Dict[str, Any]]:
            stored_history = await self.get_stored_history(user_id)
            messages_only = stored_history[1:]
            recent = messages_only[-self._settings.history_max_messages :]

            payload = [
                {"role": "system", "content": self._settings.system_prompt},
                *recent,
            ]
            await self._redis.set(
                self._key(user_id),
                json.dumps(payload),
                ex=self._settings.redis_history_ttl,
            )
            return recent

        # recent_messages is intentionally ignored for correctness under concurrency.
        return await self._with_user_lock(user_id, _work)

    def _trim_history(self, history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        system, messages = history[0], history[1:]
        max_len = self._settings.history_store_messages
        return [system, *self._trim_messages(messages, max_len)]

    def _trim_messages(
        self, messages: List[Dict[str, Any]], max_len: int
    ) -> List[Dict[str, Any]]:
        if len(messages) <= max_len:
            return messages
        return messages[-max_len:]

    def _initial_history(self) -> List[Dict[str, Any]]:
        return [{"role": "system", "content": self._settings.system_prompt}]

    def _ensure_system_prompt(self, history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not history:
            return self._initial_history()
        if history[0].get("role") != "system":
            history.insert(0, self._initial_history()[0])
        else:
            history[0]["content"] = self._settings.system_prompt
        return history
