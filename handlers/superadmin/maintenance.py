import html
import logging
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from handlers.admin.filters import IsSuperAdmin
from handlers.admin.ui import safe_edit
from services.audit import write as audit_write
from services.config_live import get as cfg_get, set_value as cfg_set

log = logging.getLogger(__name__)
router = Router(name="sa_maintenance")
router.message.filter(IsSuperAdmin())
router.callback_query.filter(IsSuperAdmin())

MAINT_KEY = "maintenance_mode"
MAINT_MSG_KEY = "maintenance_message"
MAINT_UNTIL_KEY = "maintenance_until"
DEFAULT_MSG = "🛠 Бот на обслуживании, попробуй позже."


class MaintStates(StatesGroup):
    edit_message = State()


def _format_until(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%d.%m.%Y %H:%M")


@router.callback_query(F.data == "sa:maint:menu")
async def cb_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    on = bool(await cfg_get(MAINT_KEY, False))
    msg = await cfg_get(MAINT_MSG_KEY, DEFAULT_MSG)
    until_raw = await cfg_get(MAINT_UNTIL_KEY, None)
    until_label = _format_until(until_raw) if on else None

    rows: list[list[InlineKeyboardButton]] = []
    if on:
        rows.append([InlineKeyboardButton(text="🛑 Выключить", callback_data="sa:maint:off")])
    else:
        rows.append([
            InlineKeyboardButton(text="✅ Включить навсегда", callback_data="sa:maint:on:0"),
        ])
        rows.append([
            InlineKeyboardButton(text="⏱ 30 мин", callback_data="sa:maint:on:30"),
            InlineKeyboardButton(text="⏱ 1 ч", callback_data="sa:maint:on:60"),
            InlineKeyboardButton(text="⏱ 4 ч", callback_data="sa:maint:on:240"),
        ])
    rows.append([InlineKeyboardButton(text="✏️ Сообщение", callback_data="sa:maint:msg")])
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="sa:menu")])

    state_label = "🟢 ВКЛЮЧЕН" if on else "⚪️ выключен"
    text = (
        f"🛠 <b>Режим обслуживания</b>\n\n"
        f"Состояние: {state_label}\n"
    )
    if until_label:
        text += f"Авто-выключение: {until_label}\n"
    text += f"Сообщение: «{html.escape(msg)}»"

    await safe_edit(call, text, InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


@router.callback_query(F.data.startswith("sa:maint:on:"))
async def cb_on(call: CallbackQuery, state: FSMContext) -> None:
    try:
        minutes = int(call.data.rsplit(":", 1)[-1])
    except ValueError:
        await call.answer("Битый callback", show_alert=True)
        return
    await cfg_set(MAINT_KEY, "1", "bool", "Maintenance mode", call.from_user.id)
    if minutes > 0:
        until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        await cfg_set(MAINT_UNTIL_KEY, until.isoformat(), "str",
                      "Maintenance auto-off timestamp (ISO)", call.from_user.id)
        await audit_write(
            call.from_user.id, "maintenance.on", "config", MAINT_KEY,
            {"until": until.isoformat(), "minutes": minutes},
        )
    else:
        await cfg_set(MAINT_UNTIL_KEY, None, "str",
                      "Maintenance auto-off timestamp (ISO)", call.from_user.id)
        await audit_write(call.from_user.id, "maintenance.on", "config", MAINT_KEY, {"minutes": 0})
    await cb_menu(call, state)


@router.callback_query(F.data == "sa:maint:off")
async def cb_off(call: CallbackQuery, state: FSMContext) -> None:
    await cfg_set(MAINT_KEY, "0", "bool", "Maintenance mode", call.from_user.id)
    await cfg_set(MAINT_UNTIL_KEY, None, "str",
                  "Maintenance auto-off timestamp (ISO)", call.from_user.id)
    await audit_write(call.from_user.id, "maintenance.off", "config", MAINT_KEY, None)
    await cb_menu(call, state)


@router.callback_query(F.data == "sa:maint:msg")
async def cb_msg_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(MaintStates.edit_message)
    cur = await cfg_get(MAINT_MSG_KEY, DEFAULT_MSG)
    await call.message.answer(
        "Введи новое сообщение для пользователей в режиме обслуживания.\n"
        f"Сейчас: «{html.escape(cur)}»\n"
        "Отмена: /cancel\nСбросить к дефолту: /reset",
    )
    await call.answer()


@router.message(MaintStates.edit_message, F.text == "/cancel")
async def msg_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено.")


@router.message(MaintStates.edit_message, F.text == "/reset")
async def msg_reset(message: Message, state: FSMContext) -> None:
    await cfg_set(MAINT_MSG_KEY, DEFAULT_MSG, "str",
                  "Maintenance user-facing message", message.from_user.id)
    await audit_write(
        message.from_user.id, "maintenance.message", "config", MAINT_MSG_KEY,
        {"value": DEFAULT_MSG},
    )
    await state.clear()
    await message.answer("✅ Сообщение сброшено к дефолту.")


@router.message(MaintStates.edit_message, F.text)
async def msg_set(message: Message, state: FSMContext) -> None:
    raw = message.text.strip()
    if not (1 <= len(raw) <= 1000):
        await message.answer("Длина 1–1000 символов. Повтори.")
        return
    await cfg_set(MAINT_MSG_KEY, raw, "str",
                  "Maintenance user-facing message", message.from_user.id)
    await audit_write(
        message.from_user.id, "maintenance.message", "config", MAINT_MSG_KEY,
        {"value": raw[:200]},
    )
    await state.clear()
    await message.answer("✅ Сообщение обновлено.")
