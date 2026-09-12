"""Teleport-aware rendering of menu messages.

`render` перерисовывает меню в том же сообщении. Если в чате есть более
свежие сообщения бота (уведомление о завозе, рассылка и т.п. — см.
services.chat_journal), то меню «телепортируется» в самый низ чата: старое
сообщение удаляется, новое уходит под эти сообщения. Так в переписке
сохраняется естественный порядок: уведомление сверху, меню под ним.
"""
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup

from services.chat_journal import journal

log = logging.getLogger(__name__)


async def render(
    bot: Bot,
    chat_id: int,
    message_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    **edit_kwargs,
) -> int:
    """Teleport-aware edit. Возвращает message_id, где теперь живёт меню."""
    if journal.has_newer(chat_id, message_id):
        return await _teleport(bot, chat_id, message_id, text, reply_markup, edit_kwargs)

    try:
        await bot.edit_message_text(
            text,
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=reply_markup,
            **edit_kwargs,
        )
        return message_id
    except TelegramBadRequest as exc:
        # "chat not modified": кликнули кнопку, контент уже тот же — ок.
        if "message is not modified" in str(exc):
            return message_id
        # Меню уже удалили (телепорт/удаление вручную) — шлём заново.
        if "message to edit not found" in str(exc) or "there is no message" in str(exc):
            return await _teleport(bot, chat_id, message_id, text, reply_markup, edit_kwargs)
        raise


async def _teleport(
    bot: Bot,
    chat_id: int,
    message_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None,
    edit_kwargs: dict,
) -> int:
    try:
        await bot.delete_message(chat_id, message_id)
    except TelegramBadRequest:
        pass
    journal.drop_below(chat_id, message_id)
    msg = await bot.send_message(chat_id, text, reply_markup=reply_markup, **edit_kwargs)
    journal.record(chat_id, msg.message_id)
    return msg.message_id