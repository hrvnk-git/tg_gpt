from __future__ import annotations

import asyncio
import logging

import redis.asyncio as redis
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.access_control import AccessControl
from app.config import get_settings
from app.memory import ConversationMemory
from app.openai_client import OpenAIClient
from app.rate_limiter import RateLimiter
from app.routers.admin import create_admin_router
from app.routers.chat import create_chat_router

logging.basicConfig(level=logging.INFO)


async def main() -> None:
    settings = get_settings()
    redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    access = AccessControl(redis_client, settings)
    await access.seed_if_empty()

    memory = ConversationMemory(redis_client, settings)
    rate_limiter = RateLimiter(
        redis_client, settings.rate_limit, settings.rate_window_seconds
    )
    ai_client = OpenAIClient(settings.openai_api_key)

    bot = Bot(
        token=settings.telegram_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    dp.include_router(
        create_chat_router(memory, rate_limiter, ai_client, settings, access)
    )
    dp.include_router(create_admin_router(access))

    logging.info("Bot is starting")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        await redis_client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot stopped")
