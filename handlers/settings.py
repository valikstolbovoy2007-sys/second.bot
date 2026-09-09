import logging
from datetime import date, timedelta

from aiogram import F, Router
from aiogram.types import CallbackQuery

from data.repos.users import (
    get_settings,
    set_pause_until,
    toggle_notify_arrival,
    toggle_notify_cheap_day,
    upsert_user,
)
from keyboards.settings_kb import (
    PauseCb,
    SettingsCb,
    pause_picker_kb,
    settings_menu_kb,
)
from services.texts import t

log = logging.getLogger(__name__)
router = Router(name="settings")


async def _settings_text(pause_until: date | None) -> str:
    lines = [await t("settings.title"), ""]
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
        await _settings_text(s.pause_until),
        reply_markup=settings_menu_kb(s.pause_until, s.notify_arrival, s.notify_cheap_day),
    )
    await call.answer()


@router.callback_query(F.data == "settings:open")
async def cb_open(call: CallbackQuery) -> None:
    await _render_settings(call)


@router.callback_query(SettingsCb.filter(F.action == "open"))
async def cb_back(call: CallbackQuery, callback_data: SettingsCb) -> None:
    await _render_settings(call)


@router.callback_query(SettingsCb.filter(F.action == "toggle_arrival"))
async def cb_toggle_arrival(call: CallbackQuery, callback_data: SettingsCb) -> None:
    user_id = await upsert_user(call.from_user.id, call.from_user.username)
    new_value = await toggle_notify_arrival(user_id)
    await call.answer(await t("settings.toggle_on" if new_value else "settings.toggle_off"))
    await _render_settings(call)


@router.callback_query(SettingsCb.filter(F.action == "toggle_cheap"))
async def cb_toggle_cheap(call: CallbackQuery, callback_data: SettingsCb) -> None:
    user_id = await upsert_user(call.from_user.id, call.from_user.username)
    new_value = await toggle_notify_cheap_day(user_id)
    await call.answer(await t("settings.toggle_on" if new_value else "settings.toggle_off"))
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
