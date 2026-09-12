from aiogram.types import CallbackQuery, InlineKeyboardMarkup

from services.chat_render import render


async def safe_edit(
    call: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> int:
    """Teleport-aware перерисовка меню. Возвращает актуальный message_id."""
    return await render(
        call.bot, call.message.chat.id, call.message.message_id, text, reply_markup,
    )