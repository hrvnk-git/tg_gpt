from __future__ import annotations

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from app.access_control import AccessControl

from .utils import TELEGRAM_MESSAGE_MAX_CHARS, render_telegram_html, split_text


class AdminStates(StatesGroup):
    waiting_for_add_user_id = State()
    waiting_for_remove_user_id = State()


def _parse_user_id(text: str) -> int | None:
    text = (text or "").strip()
    if not text:
        return None
    # Accept either raw number; only number-based allowlist is supported.
    try:
        return int(text)
    except ValueError:
        return None


def create_admin_router(access: AccessControl) -> Router:
    router = Router()

    @router.message(Command("admin"))
    async def handle_admin(message, state: FSMContext) -> None:
        from_user = message.from_user
        if from_user is None:
            return

        if not await access.is_admin(from_user.id):
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
        from_user = call.from_user
        if from_user is None:
            await call.answer("Недоступно для этого события.", show_alert=True)
            return

        if not await access.is_admin(from_user.id):
            await call.answer("Доступ запрещен.", show_alert=True)
            return

        if call.message is None:
            await call.answer("Не удалось отправить сообщение.", show_alert=True)
            return

        await state.set_state(AdminStates.waiting_for_add_user_id)
        await call.message.answer(
            render_telegram_html("Отправь `user_id` (число), которого добавить:"),
            parse_mode=ParseMode.HTML,
        )
        await call.answer()

    @router.callback_query(F.data == "admin:remove")
    async def cb_admin_remove(call: CallbackQuery, state: FSMContext) -> None:
        from_user = call.from_user
        if from_user is None:
            await call.answer("Недоступно для этого события.", show_alert=True)
            return

        if not await access.is_admin(from_user.id):
            await call.answer("Доступ запрещен.", show_alert=True)
            return

        if call.message is None:
            await call.answer("Не удалось отправить сообщение.", show_alert=True)
            return

        await state.set_state(AdminStates.waiting_for_remove_user_id)
        await call.message.answer(
            render_telegram_html("Отправь `user_id` (число), которого удалить:"),
            parse_mode=ParseMode.HTML,
        )
        await call.answer()

    @router.callback_query(F.data == "admin:list")
    async def cb_admin_list(call: CallbackQuery) -> None:
        from_user = call.from_user
        if from_user is None:
            await call.answer("Недоступно для этого события.", show_alert=True)
            return

        if not await access.is_admin(from_user.id):
            await call.answer("Доступ запрещен.", show_alert=True)
            return

        if call.message is None:
            await call.answer("Не удалось отправить сообщение.", show_alert=True)
            return

        allowed = await access.list_allowed_users(limit=50)
        if not allowed:
            await call.message.answer("Список пользователей пуст.")
            await call.answer()
            return

        # HTML links to personal chats: tg://user?id=...
        lines = [f'<a href="tg://user?id={uid}">{uid}</a>' for uid in allowed]
        text = "Разрешенные пользователи (до 50):\n" + "\n".join(lines)

        for part in split_text(text, TELEGRAM_MESSAGE_MAX_CHARS):
            await call.message.answer(
                part,
                parse_mode=ParseMode.HTML
            )
        await call.answer()

    @router.message(StateFilter(AdminStates.waiting_for_add_user_id))
    async def add_user_from_state(message, state: FSMContext) -> None:
        from_user = message.from_user
        if from_user is None:
            await state.clear()
            return

        if not await access.is_admin(from_user.id):
            await message.answer("Доступ запрещен.")
            await state.clear()
            return

        user_id = _parse_user_id(message.text or "")
        if user_id is None:
            await message.answer(
                render_telegram_html("Нужно число. Например: `123456`"),
                parse_mode=ParseMode.HTML,
            )
            return

        await access.add_allowed_user(user_id)
        await message.answer(
            render_telegram_html(f"Пользователь `{user_id}` добавлен."),
            parse_mode=ParseMode.HTML,
        )
        await state.clear()

    @router.message(StateFilter(AdminStates.waiting_for_remove_user_id))
    async def remove_user_from_state(message, state: FSMContext) -> None:
        from_user = message.from_user
        if from_user is None:
            await state.clear()
            return

        if not await access.is_admin(from_user.id):
            await message.answer("Доступ запрещен.")
            await state.clear()
            return

        user_id = _parse_user_id(message.text or "")
        if user_id is None:
            await message.answer(
                render_telegram_html("Нужно число. Например: `123456`"),
                parse_mode=ParseMode.HTML,
            )
            return

        await access.remove_allowed_user(user_id)
        await message.answer(
            render_telegram_html(f"Пользователь `{user_id}` удалён."),
            parse_mode=ParseMode.HTML,
        )
        await state.clear()

    return router
