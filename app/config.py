from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv


load_dotenv()


@dataclass(slots=True)
class Settings:
    telegram_token: str
    openai_api_key: str
    redis_url: str = "redis://localhost:6379/0"
    redis_history_ttl: int = 60 * 60 * 24
    # Кол-во сообщений, которые реально отправляем в модель (без summary).
    history_max_messages: int = 20
    # Кол-во сообщений, которые храним в Redis до того, как начнём суммаризацию.
    history_store_messages: int = 60
    # Если старых сообщений больше этого порога, генерируем summary и выбрасываем "старое".
    # Если не задан — считаем как (history_store_messages - history_max_messages).
    summary_trigger_messages: int | None = None
    # Ограничение размера summary по символам (чтобы не раздувать токены).
    summary_max_chars: int = 1200
    rate_limit: int = 5
    rate_window_seconds: int = 30
    system_prompt: str = (
        "You are a helpful Telegram assistant. Answer succinctly and stay polite."
    )
    allowed_user_ids: List[int] = field(default_factory=list)
    allow_all_users: bool = True
    admin_user_ids: List[int] = field(default_factory=list)
    allow_all_admins: bool = False

    def is_user_allowed(self, user_id: int) -> bool:
        if self.allow_all_users:
            return True
        return user_id in (self.allowed_user_ids or [])

    def is_admin(self, user_id: int) -> bool:
        if self.allow_all_admins:
            return True
        return user_id in (self.admin_user_ids or [])

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")

        redis_url = os.getenv("REDIS_URL", cls.redis_url)
        redis_ttl = int(os.getenv("REDIS_HISTORY_TTL", cls.redis_history_ttl))
        history_max = int(os.getenv("HISTORY_MAX_MESSAGES", cls.history_max_messages))
        history_store = int(
            os.getenv("HISTORY_STORE_MESSAGES", cls.history_store_messages)
        )
        summary_trigger_env = os.getenv("SUMMARY_TRIGGER_MESSAGES")
        summary_trigger = (
            int(summary_trigger_env)
            if summary_trigger_env is not None
            else (history_store - history_max)
        )
        summary_max_chars = int(os.getenv("SUMMARY_MAX_CHARS", cls.summary_max_chars))
        rate_limit = int(os.getenv("RATE_LIMIT", cls.rate_limit))
        rate_window = int(os.getenv("RATE_WINDOW_SECONDS", cls.rate_window_seconds))
        prompt = os.getenv("SYSTEM_PROMPT", cls.system_prompt)

        allowed_env = os.getenv("ALLOWED_USER_IDS", "").strip()
        if not allowed_env or allowed_env == "*":
            allow_all_users = True
            allowed_user_ids: List[int] = []
        else:
            allow_all_users = False
            allowed_user_ids = [
                int(x.strip())
                for x in allowed_env.split(",")
                if x.strip()
            ]

        admin_env = os.getenv("ADMIN_USER_IDS", "").strip()
        if not admin_env:
            # по умолчанию админы = allowed_user_ids
            admin_env = ",".join(str(x) for x in allowed_user_ids)

        if not admin_env or admin_env == "*":
            allow_all_admins = True
            admin_user_ids: List[int] = []
        else:
            allow_all_admins = False
            admin_user_ids = [int(x.strip()) for x in admin_env.split(",") if x.strip()]

        return cls(
            telegram_token=token,
            openai_api_key=api_key,
            redis_url=redis_url,
            redis_history_ttl=redis_ttl,
            history_max_messages=history_max,
            history_store_messages=history_store,
            summary_trigger_messages=summary_trigger,
            summary_max_chars=summary_max_chars,
            rate_limit=rate_limit,
            rate_window_seconds=rate_window,
            system_prompt=prompt,
            allowed_user_ids=allowed_user_ids,
            allow_all_users=allow_all_users,
            admin_user_ids=admin_user_ids,
            allow_all_admins=allow_all_admins,
        )


def get_settings() -> Settings:
    return Settings.from_env()
