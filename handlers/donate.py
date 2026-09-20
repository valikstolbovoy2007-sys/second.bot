from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from services.chat_render import render
from services.texts import t
from services.workspace import ws

router = Router(name="donate")


async def donate_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 В меню", callback_data="menu:open")],
    ])


@router.callback_query(F.data == "donate:open")
async def cb_donate_open(call: CallbackQuery) -> None:
    """Экран «Пожертвования на хостинг» с кнопкой «🏠 В меню»."""
    new_id = await render(
        call.bot, call.message.chat.id, call.message.message_id,
        await t("donate.text"), await donate_kb(),
    )
    ws.set_active(call.from_user.id, new_id)
    await call.answer()