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


async def show_shop_card(
    call: CallbackQuery,
    shop: Shop,
    today: date,
    *,
    is_tracked: bool,
    kb: InlineKeyboardMarkup,
) -> None:
    body = await format_shop_card(shop, today, is_tracked=is_tracked)
    photo_id = await _resolve_photo_id(shop) if len(body) <= CAPTION_LIMIT else None
    msg = call.message
    was_photo = bool(msg.photo)

    if photo_id:
        if was_photo:
            try:
                await msg.edit_media(
                    InputMediaPhoto(media=photo_id, caption=body),
                    reply_markup=kb,
                )
                return
            except TelegramBadRequest as exc:
                if "message is not modified" in str(exc):
                    # caption + media + kb identical — also try a kb-only edit
                    try:
                        await msg.edit_reply_markup(reply_markup=kb)
                    except TelegramBadRequest:
                        pass
                    return
                # fall through: delete and resend
                log.debug("edit_media failed (%s), resending", exc)
        await _delete(call)
        await msg.answer_photo(photo_id, caption=body, reply_markup=kb)
        return

    # No photo path
    if was_photo:
        await _delete(call)
        await msg.answer(body, reply_markup=kb, disable_web_page_preview=True)
        return
    try:
        await msg.edit_text(body, reply_markup=kb, disable_web_page_preview=True)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise


async def show_text_view(
    call: CallbackQuery,
    text: str,
    kb: InlineKeyboardMarkup,
) -> None:
    """Render a plain-text screen, correctly handling a previous photo card."""
    msg = call.message
    if msg.photo:
        await _delete(call)
        await msg.answer(text, reply_markup=kb, disable_web_page_preview=True)
        return
    try:
        await msg.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise
