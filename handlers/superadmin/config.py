import html
import logging

from aiogram import F, Router
from aiogram.filters.callback_data import CallbackData
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
from services.config_live import list_all, set_value

log = logging.getLogger(__name__)
router = Router(name="sa_config")
router.message.filter(IsSuperAdmin())
router.callback_query.filter(IsSuperAdmin())


class CfgCb(CallbackData, prefix="sacfg"):
    action: str
    key: str | None = None


class CfgStates(StatesGroup):
    value = State()
    new_key = State()
    new_value = State()
    new_type = State()


@router.callback_query(F.data == "sa:cfg:list")
async def cb_list(call: CallbackQuery) -> None:
    items = await list_all()
    rows = [
        [InlineKeyboardButton(
            text=f"{i['key']} = {(i['value'] or '∅')[:30]}",
            callback_data=CfgCb(action="open", key=i['key']).pack(),
        )]
        for i in items
    ]
    rows.append([InlineKeyboardButton(text="➕ Новый ключ", callback_data=CfgCb(action="new").pack())])
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="sa:menu")])
    await safe_edit(call, "⚙️ <b>Конфиг бота</b> (рантайм)", InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


@router.callback_query(CfgCb.filter(F.action == "open"))
async def cb_open(call: CallbackQuery, callback_data: CfgCb) -> None:
    items = {i["key"]: i for i in await list_all()}
    it = items.get(callback_data.key)
    if not it:
        await call.answer("Не найден", show_alert=True)
        return
    text = (
        f"⚙️ <b>{html.escape(it['key'])}</b>\n"
        f"Тип: <code>{it['type']}</code>\n"
        f"Значение: <code>{html.escape(it['value'] or '∅')}</code>\n"
        f"Описание: {html.escape(it['description'] or '—')}\n"
        f"Обновлено: {it['updated_at'].strftime('%d.%m.%Y %H:%M')}"
    )
    rows = [
        [InlineKeyboardButton(text="✏️ Изменить", callback_data=CfgCb(action="edit", key=it['key']).pack())],
        [InlineKeyboardButton(text="← К списку", callback_data="sa:cfg:list")],
    ]
    await safe_edit(call, text, InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


@router.callback_query(CfgCb.filter(F.action == "edit"))
async def cb_edit(call: CallbackQuery, callback_data: CfgCb, state: FSMContext) -> None:
    await state.set_state(CfgStates.value)
    await state.update_data(key=callback_data.key)
    await call.message.answer(f"Введи новое значение для <b>{callback_data.key}</b> (или /cancel):")
    await call.answer()


@router.message(CfgStates.value, F.text == "/cancel")
async def msg_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено.")


@router.message(CfgStates.value, F.text)
async def msg_value(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    key = data["key"]
    items = {i["key"]: i for i in await list_all()}
    type_ = items.get(key, {}).get("type", "str")
    raw = message.text.strip()
    try:
        if type_ == "int":
            int(raw)
        elif type_ == "bool":
            if raw.lower() not in ("0", "1", "true", "false", "yes", "no", "on", "off"):
                raise ValueError
    except ValueError:
        await message.answer(f"Неверный формат для типа {type_}.")
        return
    await set_value(key, raw, type_, by=message.from_user.id)
    await audit_write(message.from_user.id, "config.set", "config", key, {"value": raw})
    await state.clear()
    await message.answer(f"✅ {key} = {raw}")


@router.callback_query(CfgCb.filter(F.action == "new"))
async def cb_new(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CfgStates.new_key)
    await call.message.answer("Введи имя нового ключа (например `notifications.morning_time`):")
    await call.answer()


@router.message(CfgStates.new_key, F.text)
async def msg_new_key(message: Message, state: FSMContext) -> None:
    key = message.text.strip()
    if not key.replace(".", "").replace("_", "").isalnum() or len(key) > 80:
        await message.answer("Имя ключа: латиница/цифры/точки/подчёркивания, до 80 символов.")
        return
    await state.update_data(key=key)
    await state.set_state(CfgStates.new_type)
    rows = [[InlineKeyboardButton(text=t, callback_data=CfgCb(action="newtype", key=t).pack())]
            for t in ("str", "int", "bool", "url", "time", "json")]
    await message.answer("Тип значения:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(CfgStates.new_type, CfgCb.filter(F.action == "newtype"))
async def cb_new_type(call: CallbackQuery, callback_data: CfgCb, state: FSMContext) -> None:
    await state.update_data(type=callback_data.key)
    await state.set_state(CfgStates.new_value)
    await call.message.answer("Введи значение:")
    await call.answer()


@router.message(CfgStates.new_value, F.text)
async def msg_new_value(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await set_value(data["key"], message.text.strip(), data["type"], by=message.from_user.id)
    await audit_write(message.from_user.id, "config.create", "config", data["key"])
    await state.clear()
    await message.answer(f"✅ Создан ключ {data['key']}")
