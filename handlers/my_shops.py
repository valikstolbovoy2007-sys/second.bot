import logging
from datetime import date

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup

from data.repos.shops import Shop, get_shop
from data.repos.subs import (
    count_subscriptions,
    is_subscribed,
    list_subscribed,
    subscribe,
    subscribe_all,
    unsubscribe,
    unsubscribe_all,
)
from data.repos.users import upsert_user
from keyboards.catalog_kb import TrackCb, shop_card_kb
from keyboards.my_shops_kb import PAGE_SIZE, MyShopsCb, confirm_unsub_all_kb, my_shops_kb
from services.card_render import today_marker
from services.card_view import send_shop_card, show_shop_card, show_text_view
from services.chat_render import render
from services.maps import yandex_maps_url
from services.texts import t
from services.workspace import ws

log = logging.getLogger(__name__)
router = Router(name="my_shops")


def _today_markers(shops: list[Shop], today: date) -> dict[int, str]:
    return {s.id: m for s in shops if (m := today_marker(s, today))}


async def _myshops_payload(user_id: int, *, page: int) -> tuple[str, InlineKeyboardMarkup]:
    """(текст, клавиатура) списка «Мои магазины» (включая пустой случай)."""
    offset = page * PAGE_SIZE
    shops, total = await list_subscribed(user_id, PAGE_SIZE, offset)
    if total == 0:
        return await t("myshops.empty"), my_shops_kb([], 0, 0)
    markers = _today_markers(shops, date.today())
    return (
        await t("myshops.list_header", count=total),
        my_shops_kb(shops, page, total, markers),
    )


async def _render_list(call: CallbackQuery, page: int) -> None:
    user_id = await upsert_user(call.from_user.id, call.from_user.username)
    body, kb = await _myshops_payload(user_id, page=page)
    new_id = await show_text_view(call, body, kb)
    ws.set_home(user_id, call.message.chat.id, new_id)
    await call.answer()


async def _close_card_back_to_list(
    call: CallbackQuery, user_id: int, *, page: int,
) -> None:
    """Закрыть карточку и перерисовать список на месте (не удаляя его)."""
    try:
        await call.message.delete()
    except TelegramBadRequest:
        pass
    ws.close_card(user_id)
    home = ws.home(user_id)
    if home:
        chat_id, home_id = home
        body, kb = await _myshops_payload(user_id, page=page)
        new_id = await render(call.bot, chat_id, home_id, body, kb)
        ws.set_home(user_id, chat_id, new_id)
    else:
        await _render_list(call, page=page)
    await call.answer()


@router.callback_query(F.data == "my_shops:open")
async def cb_open(call: CallbackQuery) -> None:
    await _render_list(call, page=0)


@router.callback_query(MyShopsCb.filter(F.action == "list"))
async def cb_list(call: CallbackQuery, callback_data: MyShopsCb) -> None:
    user_id = await upsert_user(call.from_user.id, call.from_user.username)
    if ws.card(user_id) == call.message.message_id:
        await _close_card_back_to_list(call, user_id, page=callback_data.page)
        return
    await _render_list(call, page=callback_data.page)


@router.callback_query(MyShopsCb.filter(F.action == "shop"))
async def cb_shop(call: CallbackQuery, callback_data: MyShopsCb) -> None:
    user_id = await upsert_user(call.from_user.id, call.from_user.username)
    shop = await get_shop(callback_data.shop_id)
    if not shop:
        await call.answer(await t("system.shop_not_found"), show_alert=True)
        return
    tracked = await is_subscribed(user_id, shop.id)
    has_prices = bool(shop.price_start and shop.price_step is not None)
    kb = shop_card_kb(
        shop.id, tracked, src="my", page=callback_data.page,
        has_prices=has_prices,
        maps_url=yandex_maps_url(shop.address),
    )
    home = ws.home(user_id)
    pressed_on_list = home is not None and home[1] == call.message.message_id
    if pressed_on_list:
        card_id = await send_shop_card(call, shop, date.today(), is_tracked=tracked, kb=kb)
    else:
        card_id = await show_shop_card(call, shop, date.today(), is_tracked=tracked, kb=kb)
    ws.open_card(user_id, card_id)
    await call.answer()


@router.callback_query(MyShopsCb.filter(F.action == "sub_all"))
async def cb_sub_all(call: CallbackQuery, callback_data: MyShopsCb) -> None:
    user_id = await upsert_user(call.from_user.id, call.from_user.username)
    added = await subscribe_all(user_id)
    msg = await t("myshops.sub_all_done", count=added) if added else await t("myshops.sub_all_already")
    await call.answer(msg, show_alert=True)
    await _render_list(call, page=0)


@router.callback_query(MyShopsCb.filter(F.action == "unsub_all_ask"))
async def cb_unsub_all_ask(call: CallbackQuery, callback_data: MyShopsCb) -> None:
    user_id = await upsert_user(call.from_user.id, call.from_user.username)
    n = await count_subscriptions(user_id)
    await show_text_view(
        call,
        await t("myshops.unsub_all_ask", count=n),
        confirm_unsub_all_kb(),
    )
    await call.answer()


@router.callback_query(MyShopsCb.filter(F.action == "unsub_all_yes"))
async def cb_unsub_all_yes(call: CallbackQuery, callback_data: MyShopsCb) -> None:
    user_id = await upsert_user(call.from_user.id, call.from_user.username)
    removed = await unsubscribe_all(user_id)
    await call.answer(await t("myshops.unsub_all_done", count=removed), show_alert=True)
    await _render_list(call, page=0)


@router.callback_query(TrackCb.filter())
async def cb_toggle(call: CallbackQuery, callback_data: TrackCb) -> None:
    user_id = await upsert_user(call.from_user.id, call.from_user.username)
    shop = await get_shop(callback_data.shop_id)
    if not shop:
        await call.answer(await t("system.shop_not_found"), show_alert=True)
        return

    tracked = await is_subscribed(user_id, shop.id)
    if tracked:
        await unsubscribe(user_id, shop.id)
        msg = await t("myshops.removed")
        new_tracked = False
    else:
        await subscribe(user_id, shop.id)
        msg = await t("myshops.added")
        new_tracked = True

    has_prices = bool(shop.price_start and shop.price_step is not None)
    kb = shop_card_kb(
        shop.id, new_tracked,
        src=callback_data.src,
        page=callback_data.page,
        flt=callback_data.flt,
        sort=callback_data.sort,
        has_prices=has_prices,
        maps_url=yandex_maps_url(shop.address),
    )
    card_id = await show_shop_card(call, shop, date.today(), is_tracked=new_tracked, kb=kb)
    ws.open_card(user_id, card_id)
    await call.answer(msg)
