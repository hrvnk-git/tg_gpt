from __future__ import annotations

import base64
import os

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender

from app.access_control import AccessControl
from app.config import Settings
from app.memory import ConversationMemory
from app.openai_client import OpenAIClient
from app.rate_limiter import RateLimiter

from .admin import AdminStates
from .utils import TELEGRAM_MESSAGE_MAX_CHARS, render_telegram_html, split_text


def create_chat_router(
    memory: ConversationMemory,
    rate_limiter: RateLimiter,
    ai_client: OpenAIClient,
    settings: Settings,
    access: AccessControl,
) -> Router:
    router = Router()

    def _guess_image_mime(file_path: str) -> str:
        p = (file_path or "").lower()
        if p.endswith(".png"):
            return "image/png"
        if p.endswith(".webp"):
            return "image/webp"
        return "image/jpeg"

    def _guess_audio_filename(file_path: str) -> str:
        p = (file_path or "").strip()
        name = os.path.basename(p)
        return name or "voice.ogg"

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

            # Если summary-кандидат пустой — не перетираем Redis summary,
            # но и history должны строиться на основе recent сообщений.
            if summary_candidate.strip():
                summary_for_model = summary_candidate.strip()
            else:
                summary_for_model = await memory.get_summary(user_id)

            stored_history_for_model = [
                {"role": "system", "content": settings.system_prompt},
                *recent_for_model,
            ]
            history = memory.build_history(
                stored_history_for_model, summary_for_model
            )
        else:
            summary_for_model = await memory.get_summary(user_id)
            history = memory.build_history(stored_history, summary_for_model)

        async with ChatActionSender.typing(
            bot=message.bot, chat_id=message.chat.id
        ):
            final_text = await ai_client.chat_response(history)

        final_text = final_text.strip() or "🤖 Мне нечего добавить"
        for part in split_text(final_text, TELEGRAM_MESSAGE_MAX_CHARS):
            await message.answer(
                render_telegram_html(part),
                parse_mode=ParseMode.HTML,
            )

        await memory.append(user_id, "assistant", final_text)

    @router.message(F.photo)
    async def handle_photo(message: Message, state: FSMContext) -> None:
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

        photo = message.photo[-1]  # largest size is usually the last one
        tg_file = await message.bot.get_file(photo.file_id)
        file_stream = await message.bot.download_file(tg_file.file_path)
        if file_stream is None:
            await message.answer("Не удалось скачать изображение.")
            return
        file_bytes: bytes = (
            file_stream.getvalue()
            if hasattr(file_stream, "getvalue")
            else file_stream.read()
        )

        caption = (message.caption or "").strip()
        memory_text = caption if caption else "📷 Фото"

        image_mime = _guess_image_mime(tg_file.file_path)
        image_b64 = base64.b64encode(file_bytes).decode("utf-8")
        image_data_url = f"data:{image_mime};base64,{image_b64}"

        stored_history = await memory.append_and_get_stored_history(
            user_id, "user", memory_text
        )
        messages_only = stored_history[1:]

        # If too many old messages accumulated, summarize older part (text-only).
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

        async with ChatActionSender.typing(
            bot=message.bot, chat_id=message.chat.id
        ):
            final_text = await ai_client.chat_response_with_image(
                messages=history,
                image_data_url=image_data_url,
            )

        final_text = final_text.strip() or "🤖 Мне нечего добавить"
        for part in split_text(final_text, TELEGRAM_MESSAGE_MAX_CHARS):
            await message.answer(
                render_telegram_html(part),
                parse_mode=ParseMode.HTML,
            )

        await memory.append(user_id, "assistant", final_text)

    @router.message(F.voice)
    async def handle_voice(message: Message, state: FSMContext) -> None:
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

        voice = message.voice
        tg_file = await message.bot.get_file(voice.file_id)
        file_stream = await message.bot.download_file(tg_file.file_path)
        if file_stream is None:
            await message.answer("Не удалось скачать голосовое сообщение.")
            return

        audio_bytes: bytes = (
            file_stream.getvalue()
            if hasattr(file_stream, "getvalue")
            else file_stream.read()
        )
        filename = _guess_audio_filename(tg_file.file_path)

        async with ChatActionSender.typing(
            bot=message.bot, chat_id=message.chat.id
        ):
            transcribed_text = await ai_client.transcribe_audio(
                audio_bytes,
                filename=filename,
            )

            if not transcribed_text.strip():
                transcribed_text = "🗣️ Голос (не удалось распознать дословно)"

            stored_history = await memory.append_and_get_stored_history(
                user_id, "user", transcribed_text
            )
            messages_only = stored_history[1:]

            # Same summarization logic as text/photo (text-only).
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

                if summary_candidate.strip():
                    summary_for_model = summary_candidate.strip()
                else:
                    summary_for_model = await memory.get_summary(user_id)

                stored_history_for_model = [
                    {"role": "system", "content": settings.system_prompt},
                    *recent_for_model,
                ]
                history = memory.build_history(
                    stored_history_for_model, summary_for_model
                )
            else:
                summary_for_model = await memory.get_summary(user_id)
                history = memory.build_history(stored_history, summary_for_model)

            final_text = await ai_client.chat_response(history)

        final_text = final_text.strip() or "🤖 Мне нечего добавить"
        for part in split_text(final_text, TELEGRAM_MESSAGE_MAX_CHARS):
            await message.answer(
                render_telegram_html(part),
                parse_mode=ParseMode.HTML,
            )

        await memory.append(user_id, "assistant", final_text)

    return router
