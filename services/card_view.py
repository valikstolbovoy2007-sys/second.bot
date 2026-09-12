"""Shop-card rendering at the message-transport level.

Wraps `format_shop_card` and handles the photo/text dance:
  - if the shop has a photo and the rendered card fits Telegram's caption
    limit, send (or edit_media into) a photo message with the card as caption
  - otherwise fall back to a plain text message
  - across transitions photo↔text we delete-and-resend, since Telegram does
    not let us mutate a text message into a photo message in place

Telegram-specific notes:
  - `disable_web_page_preview=True` для текстовых сообщений: в карточке есть
    HTML-ссылка на Яндекс.Карты, и без флага под сообщением периодически
    появлялось бы случайное preview (например, OG-картинка Яндекса) — это
    «съезжает» вёрстку карточки. С фото-сообщениями превью и так не строится.
"""
from __future__ import annotations

import logging
from datetime import date

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InputMediaPhoto

from data.repos.shop_photos import list_photos
from data.repos.shops import Shop
from services.card_render import format_shop_card
from services.chat_journal import journal

log = logging.getLogger(__name__)

# Telegram caption limit for photo messages.
CAPTION_LIMIT = 1024


async def _resolve_photo_id(shop: Shop) -> str | None:
    photos = await list_photos(shop.id)
    return photos[0]["file_id"] if photos else None


async def _delete(call: CallbackQuery) -> None:
    try:
        await call.message.delete()
    except TelegramBadRequest:
        pass


async def _resend_photo(msg, photo_id: str, body: str, kb: InlineKeyboardMarkup) -> None:
    return await msg.answer_photo(photo_id, caption=body, reply_markup=kb)


async def _resend_text(msg, body: str, kb: InlineKeyboardMarkup) -> None:
    return await msg.answer(body, reply_markup=kb, disable_web_page_preview=True)


async def _requires_teleport(call: CallbackQuery) -> bool:
    return journal.has_newer(call.message.chat.id, call.message.message_id)


async def _teleport(call: CallbackQuery) -> None:
    await _delete(call)
    journal.drop_below(call.message.chat.id, call.message.message_id)


async def show_shop_card(
    call: CallbackQuery,
    shop: Shop,
    today: date,
    *,
    is_tracked: bool,
    kb: InlineKeyboardMarkup,
) -> int:
    """Render a shop card into the pressed message; return where it ended up."""
    body = await format_shop_card(shop, today, is_tracked=is_tracked)
    photo_id = await _resolve_photo_id(shop) if len(body) <= CAPTION_LIMIT else None
    msg = call.message
    was_photo = bool(msg.photo)

    if await _requires_teleport(call):
        # Внизу появились свежие сообщения бота (уведомление и т.п.) —
        # карточка «телепортируется» в конец чата, под них.
        await _teleport(call)
        if photo_id:
            sent = await _resend_photo(msg, photo_id, body, kb)
        else:
            sent = await _resend_text(msg, body, kb)
        journal.record(msg.chat.id, sent.message_id)
        return sent.message_id

    if photo_id:
        if was_photo:
            try:
                await msg.edit_media(
                    InputMediaPhoto(media=photo_id, caption=body),
                    reply_markup=kb,
                )
                return msg.message_id
            except TelegramBadRequest as exc:
                if "message is not modified" in str(exc):
                    # caption + media + kb identical — also try a kb-only edit
                    try:
                        await msg.edit_reply_markup(reply_markup=kb)
                    except TelegramBadRequest:
                        pass
                    return msg.message_id
                # fall through: delete and resend
                log.debug("edit_media failed (%s), resending", exc)
        await _delete(call)
        sent = await _resend_photo(msg, photo_id, body, kb)
        return sent.message_id

    # No photo path
    if was_photo:
        await _delete(call)
        sent = await _resend_text(msg, body, kb)
        return sent.message_id
    try:
        await msg.edit_text(body, reply_markup=kb, disable_web_page_preview=True)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise
    return msg.message_id


async def show_text_view(
    call: CallbackQuery,
    text: str,
    kb: InlineKeyboardMarkup,
) -> int:
    """Render a plain-text screen; return the message id it now lives in."""
    msg = call.message
    if await _requires_teleport(call):
        await _teleport(call)
        sent = await _resend_text(msg, text, kb)
        journal.record(msg.chat.id, sent.message_id)
        return sent.message_id
    if msg.photo:
        await _delete(call)
        sent = await _resend_text(msg, text, kb)
        return sent.message_id
    try:
        await msg.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise
    return msg.message_id
