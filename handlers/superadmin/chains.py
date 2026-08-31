"""Superadmin chains CRUD UI (TZ §10.3).

Lists chains, lets the operator create/edit/delete/toggle. Editing supports
all fields except is_active (toggle button).
"""
import html
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from data.repos.chains import (
    create_chain,
    delete_chain,
    get_chain,
    list_chains,
    set_active,
    shops_in_chain,
    update_chain_field,
)
from handlers.admin.filters import IsSuperAdmin
from handlers.admin.ui import safe_edit
from services.audit import write as audit_write
from states.admin_states import ChainStates

log = logging.getLogger(__name__)
router = Router(name="sa_chains")
router.message.filter(IsSuperAdmin())
router.callback_query.filter(IsSuperAdmin())

PAGE_SIZE = 10


def _back_kb(target: str = "sa:menu", text: str = "← Назад") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text, callback_data=target)]])


def _cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✖️ Отмена", callback_data="sa:chain:cancel")]])


async def _render_list(call: CallbackQuery, page: int) -> None:
    chains = await list_chains(only_active=False)
    total = len(chains)
    items = chains[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]

    lines = [f"🔗 <b>Сети</b> (всего {total})", ""]
    rows: list[list[InlineKeyboardButton]] = []
    if not items:
        lines.append("— пусто —")
    for c in items:
        marker = "🟢" if c.is_active else "⚪️"
        cycle = f"{c.default_cycle}д" if c.default_cycle else "—"
        rows.append([
            InlineKeyboardButton(
                text=f"{marker} {c.name} • {cycle}",
                callback_data=f"sa:chain:view:{c.id}",
            ),
        ])

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"sa:chain:list:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"стр {page + 1}", callback_data="noop"))
    if (page + 1) * PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"sa:chain:list:{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton(text="➕ Новая сеть", callback_data="sa:chain:new")])
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="sa:menu")])

    await safe_edit(call, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("sa:chain:list:"))
async def cb_list(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    try:
        page = int(call.data.rsplit(":", 1)[-1])
    except ValueError:
        page = 0
    await _render_list(call, page)
    await call.answer()


def _view_kb(chain_id: int, is_active: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Имя", callback_data=f"sa:chain:edit:{chain_id}:name")],
        [InlineKeyboardButton(text="🗓 Цикл", callback_data=f"sa:chain:edit:{chain_id}:default_cycle")],
        [InlineKeyboardButton(text="🔌 parser_key", callback_data=f"sa:chain:edit:{chain_id}:parser_key")],
        [InlineKeyboardButton(text="↕️ Порядок", callback_data=f"sa:chain:edit:{chain_id}:sort_order")],
        [InlineKeyboardButton(
            text="⛔️ Деактивировать" if is_active else "✅ Активировать",
            callback_data=f"sa:chain:toggle:{chain_id}",
        )],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"sa:chain:delconf:{chain_id}")],
        [InlineKeyboardButton(text="← К списку", callback_data="sa:chain:list:0")],
    ])


def _format_card(c) -> str:
    cycle = f"{c.default_cycle} дн." if c.default_cycle else "—"
    parser = c.parser_key or "—"
    return (
        f"🔗 <b>{html.escape(c.name)}</b> (id={c.id})\n\n"
        f"Активна: {'да' if c.is_active else 'нет'}\n"
        f"Цикл по умолчанию: {cycle}\n"
        f"parser_key: <code>{html.escape(parser)}</code>\n"
        f"sort_order: {c.sort_order}"
    )


@router.callback_query(F.data.startswith("sa:chain:view:"))
async def cb_view(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    chain_id = int(call.data.rsplit(":", 1)[-1])
    c = await get_chain(chain_id)
    if not c:
        await call.answer("Не найдено", show_alert=True)
        return
    await safe_edit(call, _format_card(c), _view_kb(chain_id, c.is_active))
    await call.answer()


@router.callback_query(F.data == "sa:chain:new")
async def cb_new(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(ChainStates.name)
    await call.message.answer("Введи <b>имя сети</b> (1–80 символов):", reply_markup=_cancel_kb())
    await call.answer()


@router.callback_query(F.data == "sa:chain:cancel")
async def cb_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await safe_edit(call, "Отменено.", _back_kb("sa:chain:list:0"))
    await call.answer()


@router.message(ChainStates.name, F.text)
async def msg_new_name(message: Message, state: FSMContext) -> None:
    name = message.text.strip()
    if not (1 <= len(name) <= 80):
        await message.answer("Имя 1–80 символов. Повтори.")
        return
    try:
        chain_id = await create_chain(name=name)
    except Exception as e:
        log.exception("create_chain failed")
        await message.answer(f"❌ Ошибка: {html.escape(str(e))}")
        await state.clear()
        return
    await audit_write(message.from_user.id, "chain.create", "chain", chain_id, {"name": name})
    await state.clear()
    c = await get_chain(chain_id)
    await message.answer(_format_card(c), reply_markup=_view_kb(chain_id, c.is_active))


# ---------- edit ----------

@router.callback_query(F.data.startswith("sa:chain:edit:"))
async def cb_edit(call: CallbackQuery, state: FSMContext) -> None:
    parts = call.data.split(":")
    chain_id = int(parts[3])
    field = parts[4]
    await state.set_state(ChainStates.edit_value)
    await state.update_data(chain_id=chain_id, field=field)
    prompt = {
        "name": "Введи новое имя (1–80):",
        "default_cycle": "Введи число (дней) или '-' чтобы очистить:",
        "parser_key": "Введи parser_key (например, megahand) или '-' чтобы очистить:",
        "sort_order": "Введи целое число для sort_order (0 — без приоритета):",
    }.get(field, "Введи значение:")
    await call.message.answer(prompt, reply_markup=_cancel_kb())
    await call.answer()


@router.message(ChainStates.edit_value, F.text)
async def msg_edit_value(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    chain_id = data["chain_id"]
    field = data["field"]
    raw = message.text.strip()

    value: object
    if field == "name":
        if not (1 <= len(raw) <= 80):
            await message.answer("1–80 символов. Повтори.")
            return
        value = raw
    elif field == "default_cycle":
        if raw == "-":
            value = None
        else:
            try:
                v = int(raw)
                if v <= 0 or v > 365:
                    raise ValueError
                value = v
            except ValueError:
                await message.answer("Целое число 1–365 или '-'. Повтори.")
                return
    elif field == "parser_key":
        value = None if raw == "-" else raw[:64]
    elif field == "sort_order":
        try:
            value = int(raw)
        except ValueError:
            await message.answer("Целое число. Повтори.")
            return
    else:
        await message.answer("Неизвестное поле.")
        await state.clear()
        return

    try:
        await update_chain_field(chain_id, field, value)
    except Exception as e:
        log.exception("update_chain_field failed")
        await message.answer(f"❌ Ошибка: {html.escape(str(e))}")
        await state.clear()
        return

    await audit_write(
        message.from_user.id,
        "chain.update",
        "chain",
        chain_id,
        {"field": field, "value": value},
    )
    await state.clear()
    c = await get_chain(chain_id)
    await message.answer("✅ Обновлено.\n\n" + _format_card(c), reply_markup=_view_kb(chain_id, c.is_active))


# ---------- toggle / delete ----------

@router.callback_query(F.data.startswith("sa:chain:toggle:"))
async def cb_toggle(call: CallbackQuery) -> None:
    chain_id = int(call.data.rsplit(":", 1)[-1])
    c = await get_chain(chain_id)
    if not c:
        await call.answer("Не найдено", show_alert=True)
        return
    new_active = not c.is_active
    await set_active(chain_id, new_active)
    await audit_write(call.from_user.id, "chain.toggle_active", "chain", chain_id, {"is_active": new_active})
    c = await get_chain(chain_id)
    await safe_edit(call, _format_card(c), _view_kb(chain_id, c.is_active))
    await call.answer("Готово")


@router.callback_query(F.data.startswith("sa:chain:delconf:"))
async def cb_delconf(call: CallbackQuery) -> None:
    chain_id = int(call.data.rsplit(":", 1)[-1])
    c = await get_chain(chain_id)
    if not c:
        await call.answer("Не найдено", show_alert=True)
        return
    n_shops = await shops_in_chain(c.name)
    text = (
        f"🗑 Удалить сеть <b>{html.escape(c.name)}</b>?\n\n"
        f"К ней привязано магазинов: {n_shops}\n"
        f"<i>Магазины не удаляются — у них останется поле chain_name. "
        f"Лучше деактивировать.</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Удалить", callback_data=f"sa:chain:del:{chain_id}")],
        [InlineKeyboardButton(text="← Отмена", callback_data=f"sa:chain:view:{chain_id}")],
    ])
    await safe_edit(call, text, kb)
    await call.answer()


@router.callback_query(F.data.startswith("sa:chain:del:"))
async def cb_del(call: CallbackQuery) -> None:
    chain_id = int(call.data.rsplit(":", 1)[-1])
    c = await get_chain(chain_id)
    if not c:
        await call.answer("Не найдено", show_alert=True)
        return
    await delete_chain(chain_id)
    await audit_write(call.from_user.id, "chain.delete", "chain", chain_id, {"name": c.name})
    await call.answer("Удалено")
    await _render_list(call, 0)
