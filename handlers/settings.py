import logging
from datetime import date, time, timedelta

from aiogram import F, Router
from aiogram.types import CallbackQuery

from data.repos.shops import get_shop
from data.repos.subs import (
    get_flags,
    get_weekdays,
    is_subscribed,
    toggle_flag,
    toggle_weekday,
)
from data.repos.users import (
    get_settings,
    set_notify_time,
    set_pause_until,
    upsert_user,
)
from keyboards.catalog_kb import shop_card_kb
from keyboards.settings_kb import (
    PauseCb,
    SettingsCb,
    ShopNotifCb,
    TimeCb,
    pause_picker_kb,
    settings_menu_kb,
    shop_notif_cycle_kb,
    shop_notif_weekdays_kb,
    time_picker_kb,
)
from services.card_view import show_shop_card, show_text_view
from services.maps import yandex_maps_url
from services.texts import t

log = logging.getLogger(__name__)
router = Router(name="settings")


async def _settings_text(notify_time: time, pause_until: date | None) -> str:
    lines = [await t("settings.title"), ""]
    lines.append(await t("settings.notify_time", time=notify_time.strftime("%H:%M")))
    if pause_until and pause_until > date.today():
        lines.append(await t("settings.pause_active", date=pause_until.strftime("%d.%m.%Y")))
    else:
        lines.append(await t("settings.pause_off"))
    return "\n".join(lines)


async def _render_settings(call: CallbackQuery) -> None:
    user_id = await upsert_user(call.from_user.id, call.from_user.username)
    s = await get_settings(user_id)
    assert s is not None
    await call.message.edit_text(
        await _settings_text(s.notify_time, s.pause_until),
        reply_markup=settings_menu_kb(s.notify_time, s.pause_until),
    )
    await call.answer()


@router.callback_query(F.data == "settings:open")
async def cb_open(call: CallbackQuery) -> None:
    await _render_settings(call)


@router.callback_query(SettingsCb.filter(F.action == "open"))
async def cb_back(call: CallbackQuery, callback_data: SettingsCb) -> None:
    await _render_settings(call)


@router.callback_query(SettingsCb.filter(F.action == "time"))
async def cb_time_menu(call: CallbackQuery, callback_data: SettingsCb) -> None:
    user_id = await upsert_user(call.from_user.id, call.from_user.username)
    s = await get_settings(user_id)
    assert s is not None
    await call.message.edit_text(
        await t("settings.time_picker"),
        reply_markup=time_picker_kb(s.notify_time),
    )
    await call.answer()


@router.callback_query(TimeCb.filter())
async def cb_time_pick(call: CallbackQuery, callback_data: TimeCb) -> None:
    user_id = await upsert_user(call.from_user.id, call.from_user.username)
    await set_notify_time(user_id, time(callback_data.hour, callback_data.minute))
    await call.answer(await t(
        "settings.time_saved",
        time=f"{callback_data.hour:02d}:{callback_data.minute:02d}",
    ))
    await _render_settings(call)


@router.callback_query(SettingsCb.filter(F.action == "pause"))
async def cb_pause_menu(call: CallbackQuery, callback_data: SettingsCb) -> None:
    user_id = await upsert_user(call.from_user.id, call.from_user.username)
    s = await get_settings(user_id)
    assert s is not None
    active = bool(s.pause_until and s.pause_until > date.today())
    await call.message.edit_text(
        await t("settings.pause_picker"),
        reply_markup=pause_picker_kb(active),
    )
    await call.answer()


@router.callback_query(PauseCb.filter())
async def cb_pause_pick(call: CallbackQuery, callback_data: PauseCb) -> None:
    user_id = await upsert_user(call.from_user.id, call.from_user.username)
    if callback_data.days == 0:
        await set_pause_until(user_id, None)
        await call.answer(await t("settings.pause_off_done"))
    else:
        until = date.today() + timedelta(days=callback_data.days)
        await set_pause_until(user_id, until)
        await call.answer(await t("settings.pause_set_done", date=until.strftime("%d.%m")))
    await _render_settings(call)


# --- Per-shop notification settings ---

@router.callback_query(ShopNotifCb.filter(F.action == "open"))
async def cb_shop_notif_open(call: CallbackQuery, callback_data: ShopNotifCb) -> None:
    user_id = await upsert_user(call.from_user.id, call.from_user.username)
    shop = await get_shop(callback_data.shop_id)
    if not shop:
        await call.answer(await t("system.shop_not_found"), show_alert=True)
        return
    if not await is_subscribed(user_id, shop.id):
        await call.answer(await t("settings.need_track"), show_alert=True)
        return

    title = await t("settings.shop_notif_title", shop_name=shop.name)
    if shop.cycle_length and shop.anchor_date:
        flags = await get_flags(user_id, shop.id)
        assert flags is not None
        text = f"{title}\n\n{await t('settings.shop_notif_cycle')}"
        kb = shop_notif_cycle_kb(
            shop.id,
            {
                "notify_arrival": flags.notify_arrival,
                "notify_max_discount": flags.notify_max_discount,
                "notify_middle": flags.notify_middle,
            },
            src=callback_data.src, page=callback_data.page,
        )
    else:
        days = await get_weekdays(user_id, shop.id)
        text = f"{title}\n\n{await t('settings.shop_notif_wdays')}"
        kb = shop_notif_weekdays_kb(shop.id, days, src=callback_data.src, page=callback_data.page)
    await show_text_view(call, text, kb)
    await call.answer()


@router.callback_query(ShopNotifCb.filter(F.action == "tog"))
async def cb_shop_notif_toggle(call: CallbackQuery, callback_data: ShopNotifCb) -> None:
    user_id = await upsert_user(call.from_user.id, call.from_user.username)
    new_value = await toggle_flag(user_id, callback_data.shop_id, callback_data.value)
    flags = await get_flags(user_id, callback_data.shop_id)
    assert flags is not None
    shop = await get_shop(callback_data.shop_id)
    assert shop is not None
    kb = shop_notif_cycle_kb(
        shop.id,
        {
            "notify_arrival": flags.notify_arrival,
            "notify_max_discount": flags.notify_max_discount,
            "notify_middle": flags.notify_middle,
        },
        src=callback_data.src, page=callback_data.page,
    )
    await call.message.edit_reply_markup(reply_markup=kb)
    await call.answer(await t("settings.toggle_on" if new_value else "settings.toggle_off"))


@router.callback_query(ShopNotifCb.filter(F.action == "twd"))
async def cb_shop_notif_weekday(call: CallbackQuery, callback_data: ShopNotifCb) -> None:
    user_id = await upsert_user(call.from_user.id, call.from_user.username)
    weekday = int(callback_data.value)
    enabled = await toggle_weekday(user_id, callback_data.shop_id, weekday)
    days = await get_weekdays(user_id, callback_data.shop_id)
    kb = shop_notif_weekdays_kb(
        callback_data.shop_id, days, src=callback_data.src, page=callback_data.page,
    )
    await call.message.edit_reply_markup(reply_markup=kb)
    await call.answer(await t("settings.toggle_on" if enabled else "settings.toggle_off"))


@router.callback_query(ShopNotifCb.filter(F.action == "back"))
async def cb_shop_notif_back(call: CallbackQuery, callback_data: ShopNotifCb) -> None:
    user_id = await upsert_user(call.from_user.id, call.from_user.username)
    shop = await get_shop(callback_data.shop_id)
    if not shop:
        await call.answer(await t("system.shop_not_found"), show_alert=True)
        return
    tracked = await is_subscribed(user_id, shop.id)
    has_prices = bool(shop.price_start and shop.price_step is not None)
    kb = shop_card_kb(
        shop.id, tracked,
        src=callback_data.src, page=callback_data.page,
        has_prices=has_prices,
        maps_url=yandex_maps_url(shop.address),
    )
    await show_shop_card(call, shop, date.today(), is_tracked=tracked, kb=kb)
    await call.answer()
