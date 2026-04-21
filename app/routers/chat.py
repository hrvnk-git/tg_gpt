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
from .utils import TELEGRAM_MESSAGE_MAX_CHARS, render_safe_parts


def create_chat_router(
    memory: ConversationMemory,
    rate_limiter: RateLimiter,
    ai_client: OpenAIClient,
    settings: Settings,
    access: AccessControl,
) -> Router:
    router = Router()

    def _normalize_user_memory_text(text: str, *, fallback: str) -> str:
        normalized = " ".join((text or "").split()).strip()
        if not normalized:
            normalized = fallback
        return normalized[: settings.max_user_input_chars]

    async def _build_model_history(
        user_id: int,
        stored_history: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        messages_only = stored_history[1:]
        if len(messages_only) <= settings.summary_trigger_messages:
            summary_for_model = await memory.get_summary(user_id)
            return memory.build_history(stored_history, summary_for_model)

        cut = len(messages_only) - settings.history_max_messages
        older = messages_only[:cut]

        existing_summary = await memory.get_summary(user_id)
        summary_candidate = await ai_client.summarize_messages(
            system_prompt=settings.system_prompt,
            messages_to_summarize=older,
            model=settings.summary_model,
            max_summary_chars=settings.summary_max_chars,
            existing_summary=existing_summary,
            reasoning_effort=settings.openai_reasoning_effort,
            store=settings.openai_store,
        )
        await memory.set_summary(user_id, summary_candidate)
        recent_for_model = await memory.set_recent_history(user_id)

        summary_for_model = summary_candidate.strip() or existing_summary
        stored_history_for_model = [
            {"role": "system", "content": settings.system_prompt},
            *recent_for_model,
        ]
        return memory.build_history(stored_history_for_model, summary_for_model)

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
        if not name:
            return "voice.ogg"

        low = name.lower()
        # Telegram voice often comes as `.oga` (Opus in OGG container).
        # OpenAI expects supported extensions (e.g. `.ogg`), so normalize.
        if low.endswith(".oga"):
            return name[: -len(".oga")] + ".ogg"
        if low.endswith(".opus"):
            return name[: -len(".opus")] + ".ogg"

        return name

    @router.message(CommandStart())
    async def handle_start(message: Message) -> None:
        from_user = message.from_user
        if from_user is None:
            return

        user_id = from_user.id
        if not await access.is_user_allowed(user_id):
            await message.answer("Доступ запрещен.")
            return

        await memory.reset(user_id)
        await message.answer(
            "Привет! Я GPT-бот. Просто напиши сообщение, и я отвечу.\n"
            "Команда /reset очистит контекст диалога."
        )

    @router.message(Command("reset"))
    async def handle_reset(message: Message) -> None:
        from_user = message.from_user
        if from_user is None:
            return

        user_id = from_user.id
        if not await access.is_user_allowed(user_id):
            await message.answer("Доступ запрещен.")
            return

        await memory.reset(user_id)
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

        from_user = message.from_user
        if from_user is None:
            return

        user_id = from_user.id
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
            user_id,
            "user",
            _normalize_user_memory_text(
                message.text or "",
                fallback="Пользователь отправил пустое текстовое сообщение.",
            ),
        )
        history = await _build_model_history(user_id, stored_history)

        async with ChatActionSender.typing(
            bot=message.bot, chat_id=message.chat.id
        ):
            final_text = await ai_client.chat_response(
                history,
                model=settings.chat_model,
                reasoning_effort=settings.openai_reasoning_effort,
                store=settings.openai_store,
            )

        final_text = final_text.strip() or "🤖 Мне нечего добавить"
        for part in render_safe_parts(final_text, TELEGRAM_MESSAGE_MAX_CHARS):
            await message.answer(
                part,
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

        from_user = message.from_user
        if from_user is None:
            return

        user_id = from_user.id
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
        memory_text = _normalize_user_memory_text(
            caption,
            fallback="Пользователь отправил фото без подписи.",
        )

        image_mime = _guess_image_mime(tg_file.file_path)
        image_b64 = base64.b64encode(file_bytes).decode("utf-8")
        image_data_url = f"data:{image_mime};base64,{image_b64}"

        stored_history = await memory.append_and_get_stored_history(
            user_id, "user", memory_text
        )
        history = await _build_model_history(user_id, stored_history)

        async with ChatActionSender.typing(
            bot=message.bot, chat_id=message.chat.id
        ):
            final_text = await ai_client.chat_response_with_image(
                messages=history,
                image_data_url=image_data_url,
                model=settings.vision_model,
                reasoning_effort=settings.openai_reasoning_effort,
                store=settings.openai_store,
            )

        final_text = final_text.strip() or "🤖 Мне нечего добавить"
        for part in render_safe_parts(final_text, TELEGRAM_MESSAGE_MAX_CHARS):
            await message.answer(
                part,
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

        from_user = message.from_user
        if from_user is None:
            return

        user_id = from_user.id
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
                transcribed_text = "Пользователь отправил голосовое сообщение без распознаваемого текста."

            transcribed_text = _normalize_user_memory_text(
                transcribed_text,
                fallback="Пользователь отправил голосовое сообщение.",
            )

            stored_history = await memory.append_and_get_stored_history(
                user_id, "user", transcribed_text
            )
            history = await _build_model_history(user_id, stored_history)

            final_text = await ai_client.chat_response(
                history,
                model=settings.chat_model,
                reasoning_effort=settings.openai_reasoning_effort,
                store=settings.openai_store,
            )

        final_text = final_text.strip() or "🤖 Мне нечего добавить"
        for part in render_safe_parts(final_text, TELEGRAM_MESSAGE_MAX_CHARS):
            await message.answer(
                part,
                parse_mode=ParseMode.HTML,
            )

        await memory.append(user_id, "assistant", final_text)

    return router
