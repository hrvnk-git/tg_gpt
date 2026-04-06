from __future__ import annotations

import asyncio
import html
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.filters import StateFilter
import redis.asyncio as redis

from app.config import get_settings
from app.memory import ConversationMemory
from app.access_control import AccessControl
from app.openai_client import OpenAIClient
from app.rate_limiter import RateLimiter
from app.config import Settings

logging.basicConfig(level=logging.INFO)

TELEGRAM_MESSAGE_MAX_CHARS = 4096


def build_router(
    memory: ConversationMemory,
    rate_limiter: RateLimiter,
    ai_client: OpenAIClient,
    settings: Settings,
    access: AccessControl,
) -> Router:
    router = Router()

    class AdminStates(StatesGroup):
        waiting_for_add_user_id = State()
        waiting_for_remove_user_id = State()

    @router.message(CommandStart())
    async def handle_start(message: Message) -> None:
        if not await access.is_user_allowed(message.from_user.id):
            await message.answer("Доступ запрещен.")
            return
        await memory.reset(message.from_user.id)
        await message.answer(
            "Привет! Я GPT-бот. Просто напиши сообщение, и я отвечу.\n"
            "Команда /reset очистит контекст диалога."
        )

    @router.message(Command("reset"))
    async def handle_reset(message: Message) -> None:
        if not await access.is_user_allowed(message.from_user.id):
            await message.answer("Доступ запрещен.")
            return
        await memory.reset(message.from_user.id)
        await message.answer("Контекст очищен. Начнём заново!")

    @router.message(Command("admin"))
    async def handle_admin(message: Message, state: FSMContext) -> None:
        if not await access.is_admin(message.from_user.id):
            await message.answer("Доступ запрещен.")
            return
        await state.clear()
        await message.answer(
            "Админ-меню:",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Добавить user",
                            callback_data="admin:add",
                        ),
                        InlineKeyboardButton(
                            text="Удалить user",
                            callback_data="admin:remove",
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            text="Список users",
                            callback_data="admin:list",
                        )
                    ],
                ]
            ),
        )

    @router.callback_query(F.data == "admin:add")
    async def cb_admin_add(call: CallbackQuery, state: FSMContext) -> None:
        if not await access.is_admin(call.from_user.id):
            await call.answer("Доступ запрещен.", show_alert=True)
            return
        await state.set_state(AdminStates.waiting_for_add_user_id)
        await call.message.answer("Отправь `user_id` (число), которого добавить:")
        await call.answer()

    @router.callback_query(F.data == "admin:remove")
    async def cb_admin_remove(call: CallbackQuery, state: FSMContext) -> None:
        if not await access.is_admin(call.from_user.id):
            await call.answer("Доступ запрещен.", show_alert=True)
            return
        await state.set_state(AdminStates.waiting_for_remove_user_id)
        await call.message.answer("Отправь `user_id` (число), которого удалить:")
        await call.answer()

    @router.callback_query(F.data == "admin:list")
    async def cb_admin_list(call: CallbackQuery) -> None:
        if not await access.is_admin(call.from_user.id):
            await call.answer("Доступ запрещен.", show_alert=True)
            return

        allowed = await access.list_allowed_users(limit=50)
        if not allowed:
            await call.message.answer("Список пользователей пуст.")
            await call.answer()
            return

        # HTML-ссылки на личный чат: tg://user?id=...
        lines = []
        for uid in allowed:
            lines.append(f'<a href="tg://user?id={uid}">{uid}</a>')

        text = "Разрешенные пользователи (до 50):\n" + "\n".join(lines)
        # Telegram ограничивает длину сообщения (4096 символов).
        for part in _split_text(text, TELEGRAM_MESSAGE_MAX_CHARS):
            await call.message.answer(
                part,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        await call.answer()

    def _parse_user_id(text: str) -> int | None:
        text = (text or "").strip()
        if not text:
            return None
        # accept either raw number or "@username"? we only support number for allowlist.
        try:
            return int(text)
        except ValueError:
            return None

    @router.message(StateFilter(AdminStates.waiting_for_add_user_id))
    async def add_user_from_state(message: Message, state: FSMContext) -> None:
        if not await access.is_admin(message.from_user.id):
            await message.answer("Доступ запрещен.")
            await state.clear()
            return
        user_id = _parse_user_id(message.text or "")
        if user_id is None:
            await message.answer("Нужно число. Например: `123456`")
            return
        await access.add_allowed_user(user_id)
        await message.answer(f"Пользователь `{user_id}` добавлен.")
        await state.clear()

    @router.message(StateFilter(AdminStates.waiting_for_remove_user_id))
    async def remove_user_from_state(message: Message, state: FSMContext) -> None:
        if not await access.is_admin(message.from_user.id):
            await message.answer("Доступ запрещен.")
            await state.clear()
            return
        user_id = _parse_user_id(message.text or "")
        if user_id is None:
            await message.answer("Нужно число. Например: `123456`")
            return
        await access.remove_allowed_user(user_id)
        await message.answer(f"Пользователь `{user_id}` удалён.")
        await state.clear()

    @router.message(F.text & ~F.text.startswith("/"))
    async def handle_message(message: Message, state: FSMContext) -> None:
        current_state = await state.get_state()
        if current_state in {
            AdminStates.waiting_for_add_user_id.state,
            AdminStates.waiting_for_remove_user_id.state,
        }:
            # Don't let the generic handler clobber FSM admin input.
            return

        user_id = message.from_user.id
        if not await access.is_user_allowed(user_id):
            await message.answer("Доступ запрещен.")
            return
        if not await rate_limiter.allow(user_id):
            retry_after = await rate_limiter.time_to_reset(user_id)
            await message.answer(
                f"Слишком много запросов. Попробуй снова через {retry_after} сек."
            )
            return

        stored_history = await memory.append_and_get_stored_history(
            user_id, "user", message.text or ""
        )
        messages_only = stored_history[1:]

        # Если накопилось слишком много старых сообщений — суммируем выбывающую часть.
        if len(messages_only) > settings.summary_trigger_messages:
            cut = len(messages_only) - settings.history_max_messages
            older = messages_only[:cut]
            recent = messages_only[-settings.history_max_messages :]

            summary_candidate = await ai_client.summarize_messages(
                system_prompt=settings.system_prompt,
                messages_to_summarize=older,
                max_summary_chars=settings.summary_max_chars,
            )
            await memory.set_summary(user_id, summary_candidate)
            recent_for_model = await memory.set_recent_history(user_id, recent)

            # Важно: если summary-кандидат пустой — не перетираем Redis summary,
            # но и history должны строиться на основе recent сообщений.
            if summary_candidate.strip():
                summary_for_model = summary_candidate.strip()
            else:
                summary_for_model = await memory.get_summary(user_id)

            stored_history_for_model = [
                {"role": "system", "content": settings.system_prompt},
                *recent_for_model,
            ]
            history = memory.build_history(stored_history_for_model, summary_for_model)
        else:
            summary_for_model = await memory.get_summary(user_id)
            history = memory.build_history(stored_history, summary_for_model)

        async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
            final_text = await ai_client.chat_response(history)

        final_text = final_text.strip() or "🤖 Мне нечего добавить"
        for part in _split_text(final_text, TELEGRAM_MESSAGE_MAX_CHARS):
            safe_part = html.escape(part, quote=True)
            await message.answer(
                safe_part,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True,
            )

        await memory.append(user_id, "assistant", final_text)

    return router

def _split_text(text: str, max_chars: int) -> list[str]:
    """
    Telegram: max message length is 4096 symbols.
    This is a best-effort splitter that prefers breaking on newline/space
    to reduce the chance of breaking words/entities.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be > 0")

    text = text or ""
    if len(text) <= max_chars:
        return [text]

    parts: list[str] = []
    start = 0
    n = len(text)

    while start < n:
        end = min(start + max_chars, n)
        if end >= n:
            parts.append(text[start:end])
            break

        chunk = text[start:end]
        # Prefer newline, then space, then hard cut.
        nl = chunk.rfind("\n")
        if nl != -1 and nl >= int(max_chars * 0.5):
            cut = nl + 1  # include newline
            parts.append(text[start : start + cut])
            start = start + cut
            continue

        sp = chunk.rfind(" ")
        if sp != -1 and sp >= int(max_chars * 0.5):
            cut = sp + 1  # include space
            parts.append(text[start : start + cut])
            start = start + cut
            continue

        parts.append(chunk)
        start = end

    return parts

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
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
    )
    dp = Dispatcher()
    dp.include_router(
        build_router(memory, rate_limiter, ai_client, settings, access)
    )

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
