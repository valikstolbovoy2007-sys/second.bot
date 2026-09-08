import logging
from datetime import date, time, timedelta

from aiogram import F, Router
from aiogram.types import CallbackQuery

from data.repos.shops import get_shop
from data.repos.subs import (
    disable_event_notify,
    get_event_settings,
    get_flags,
    get_weekdays,
    is_subscribed,
    set_event_notify,
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
    EVENT_LABELS_RU,
    PauseCb,
    SettingsCb,
    ShopNotifCb,
    TimeCb,
    event_lead_picker_kb,
    event_time_picker_kb,
    lead_days_label,
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

async def _cycle_notif_view(user_id: int, shop_id: int, src: str, page: int) -> tuple[str, object]:
    shop = await get_shop(shop_id)
    assert shop is not None
    flags = await get_flags(user_id, shop_id)
    event_settings = await get_event_settings(user_id, shop_id)
    assert flags is not None and event_settings is not None
    title = await t("settings.shop_notif_title", shop_name=shop.name)
    text = f"{title}\n\n{await t('settings.shop_notif_cycle')}"
    kb = shop_notif_cycle_kb(
        shop_id,
        {
            "notify_arrival": flags.notify_arrival,
            "notify_max_discount": flags.notify_max_discount,
            "notify_middle": flags.notify_middle,
        },
        event_settings,
        src=src, page=page,
    )
    return text, kb


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

    if (shop.cycle_length and shop.anchor_date) or shop.monthly_weekday is not None:
        text, kb = await _cycle_notif_view(user_id, shop.id, callback_data.src, callback_data.page)
    else:
        days = await get_weekdays(user_id, shop.id)
        title = await t("settings.shop_notif_title", shop_name=shop.name)
        text = f"{title}\n\n{await t('settings.shop_notif_wdays')}"
        kb = shop_notif_weekdays_kb(shop.id, days, src=callback_data.src, page=callback_data.page)
    await show_text_view(call, text, kb)
    await call.answer()


@router.callback_query(ShopNotifCb.filter(F.action == "tog"))
async def cb_shop_notif_toggle(call: CallbackQuery, callback_data: ShopNotifCb) -> None:
    user_id = await upsert_user(call.from_user.id, call.from_user.username)
    new_value = await toggle_flag(user_id, callback_data.shop_id, callback_data.value)
    _text, kb = await _cycle_notif_view(user_id, callback_data.shop_id, callback_data.src, callback_data.page)
    await call.message.edit_reply_markup(reply_markup=kb)
    await call.answer(await t("settings.toggle_on" if new_value else "settings.toggle_off"))


@router.callback_query(ShopNotifCb.filter(F.action == "evopen"))
async def cb_event_open(call: CallbackQuery, callback_data: ShopNotifCb) -> None:
    user_id = await upsert_user(call.from_user.id, call.from_user.username)
    event = callback_data.value
    if event not in EVENT_LABELS_RU:
        await call.answer("Неизвестное событие", show_alert=True)
        return
    await show_text_view(
        call,
        f"{EVENT_LABELS_RU[event]}\n\nЗа сколько дней предупреждать?",
        event_lead_picker_kb(callback_data.shop_id, event, callback_data.src, callback_data.page),
    )
    await call.answer()


@router.callback_query(ShopNotifCb.filter(F.action == "evlead"))
async def cb_event_lead(call: CallbackQuery, callback_data: ShopNotifCb) -> None:
    event, lead_str = callback_data.value.split("|")
    await show_text_view(
        call,
        f"{EVENT_LABELS_RU[event]}, {lead_days_label(int(lead_str)).lower()}\n\nВ какое время присылать?",
        event_time_picker_kb(callback_data.shop_id, event, int(lead_str), callback_data.src, callback_data.page),
    )
    await call.answer()


@router.callback_query(ShopNotifCb.filter(F.action == "evtime"))
async def cb_event_time(call: CallbackQuery, callback_data: ShopNotifCb) -> None:
    user_id = await upsert_user(call.from_user.id, call.from_user.username)
    event, lead_str, time_str = callback_data.value.split("|")
    lead = int(lead_str)
    notify_time = time.fromisoformat(time_str) if time_str else None
    await set_event_notify(user_id, callback_data.shop_id, event, lead, notify_time)

    when = lead_days_label(lead).lower()
    time_label = notify_time.strftime("%H:%M") if notify_time else "как в общих настройках"
    await call.message.answer(
        f"✅ Настроено: {EVENT_LABELS_RU[event].lower()} — {when} в {time_label}."
    )
    text, kb = await _cycle_notif_view(user_id, callback_data.shop_id, callback_data.src, callback_data.page)
    await call.message.answer(text, reply_markup=kb)
    await call.answer()


@router.callback_query(ShopNotifCb.filter(F.action == "evoff"))
async def cb_event_off(call: CallbackQuery, callback_data: ShopNotifCb) -> None:
    user_id = await upsert_user(call.from_user.id, call.from_user.username)
    event = callback_data.value
    await disable_event_notify(user_id, callback_data.shop_id, event)
    text, kb = await _cycle_notif_view(user_id, callback_data.shop_id, callback_data.src, callback_data.page)
    await show_text_view(call, text, kb)
    await call.answer(await t("settings.toggle_off"))


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
