"""Возврат экрана к карточке сешки (общий для фидбека и вопросов).

Карточка «перебрасывается» в сообщение прашивания/превью: в репорт- и
вопрос-флоу после отмены/отправки сообщение превращается обратно в
карточку магазина, а не шлётся новым (без леса сообщений в чате).
"""
from __future__ import annotations

import logging
from datetime import date

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message

from data.repos.admin_roles import shop_regular_admins
from data.repos.shops import get_shop, shop_has_price_schedule
from data.repos.subs import is_subscribed
from data.repos.users import upsert_user
from keyboards.catalog_kb import shop_card_kb
from services.card_view import send_shop_card, show_shop_card
from services.catalog import FLT_ALL, SORT_NAME
from services.maps import yandex_maps_url
from services.workspace import ws

log = logging.getLogger(__name__)


async def shop_card_kb_for(shop, is_tracked: bool, *, src: str, page: int,
                           flt: str = FLT_ALL, sort: str = SORT_NAME,
                           on_schedule: bool = False):
    """Клавиатура карточки с учётом того, есть ли у сешки админ
    (кнопка «❓ Задать вопрос сешке» показывается только тогда)."""
    has_admin = bool(await shop_regular_admins(shop.id))
    return shop_card_kb(
        shop.id, is_tracked,
        src=src, page=page, flt=flt, sort=sort,
        has_prices=shop_has_price_schedule(shop),
        maps_url=yandex_maps_url(shop.address),
        on_schedule=on_schedule,
        has_admin=has_admin,
    )


async def redraw_shop_card(call: CallbackQuery, user_id: int, data: dict) -> None:
    """Вернуть сообщение к карточке сешки («переброс» из репорт/вопрос-флоу)."""
    shop_id = data.get("shop_id")
    if not shop_id:
        try:
            await call.message.delete()
        except TelegramBadRequest:
            pass
        return
    shop = await get_shop(int(shop_id))
    if not shop:
        return
    tracked = await is_subscribed(user_id, shop.id)
    kb = await shop_card_kb_for(
        shop, tracked,
        src=data.get("src", "cat"),
        page=data.get("page", 0),
        flt=data.get("flt", FLT_ALL),
        sort=data.get("sort", SORT_NAME),
    )
    card_id = await show_shop_card(call, shop, date.today(), is_tracked=tracked, kb=kb)
    ws.open_card(user_id, card_id)


async def restore_card_fresh(message: Message, data: dict) -> None:
    """Cancel-фолбэк без колбэка: удалить прашивание и отправить карточку заново."""
    shop_id = data.get("shop_id")
    if not shop_id:
        return
    shop = await get_shop(int(shop_id))
    if not shop:
        return
    user_id = await upsert_user(message.from_user.id, message.from_user.username)
    tracked = await is_subscribed(user_id, shop.id)
    kb = await shop_card_kb_for(
        shop, tracked,
        src=data.get("src", "cat"),
        page=data.get("page", 0),
        flt=data.get("flt", FLT_ALL),
        sort=data.get("sort", SORT_NAME),
    )
    card_id = await send_shop_card(message.bot, message.chat.id, shop, date.today(),
                                   is_tracked=tracked, kb=kb)
    ws.open_card(user_id, card_id)
