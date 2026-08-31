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

from data.repos.admin_roles import (
    add_admin,
    assign_shop,
    assigned_shop_ids,
    list_admins,
    remove_admin,
    set_admin_assignments,
    set_note,
    set_role,
    shop_admins,
    unassign_shop,
)
from data.repos.shops import get_shop, list_all_shops
from handlers.admin.filters import IsSuperAdmin
from handlers.admin.ui import safe_edit
from services.audit import write as audit_write

log = logging.getLogger(__name__)
router = Router(name="sa_admins")
router.message.filter(IsSuperAdmin())
router.callback_query.filter(IsSuperAdmin())


class AdmCb(CallbackData, prefix="saadmins"):
    action: str       # list, card, role, assign, toggle, save, remove, removec, note
    tg_id: int = 0
    page: int = 0
    shop_id: int = 0


class AdmStates(StatesGroup):
    add_id = State()
    note = State()


# ---------- LIST ----------

@router.callback_query(F.data.startswith("sa:admins:list"))
@router.callback_query(AdmCb.filter(F.action == "list"))
async def cb_list(call: CallbackQuery, callback_data: AdmCb | None = None) -> None:
    admins = await list_admins()
    rows = []
    for a in admins:
        cnt = len(await assigned_shop_ids(a.tg_id))
        emoji = "🛡" if a.role == "super_admin" else "👤"
        rows.append([InlineKeyboardButton(
            text=f"{emoji} {a.tg_id} • {a.role} • {cnt} магаз",
            callback_data=AdmCb(action="card", tg_id=a.tg_id).pack(),
        )])
    rows.append([InlineKeyboardButton(text="➕ Добавить админа", callback_data=AdmCb(action="add").pack())])
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="sa:menu")])
    await safe_edit(call, "👥 <b>Админы</b>", InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


# ---------- ADD ----------

@router.callback_query(AdmCb.filter(F.action == "add"))
async def cb_add_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdmStates.add_id)
    await call.message.answer(
        "Введи tg_id нового админа (числом). Это ID Telegram-пользователя.\nОтмена: /cancel",
    )
    await call.answer()


@router.message(AdmStates.add_id, F.text == "/cancel")
async def msg_add_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено.")


@router.message(AdmStates.add_id, F.text)
async def msg_add_id(message: Message, state: FSMContext) -> None:
    raw = message.text.strip().lstrip("@")
    if not raw.lstrip("-").isdigit():
        await message.answer("Это не tg_id. Введи число.")
        return
    tg_id = int(raw)
    await add_admin(tg_id, "admin", message.from_user.id)
    await audit_write(message.from_user.id, "admin.add", "admin", tg_id, {"role": "admin"})
    await state.clear()
    rows = [[InlineKeyboardButton(
        text="📌 Назначить магазины",
        callback_data=AdmCb(action="assign", tg_id=tg_id).pack(),
    )]]
    await message.answer(
        f"✅ Админ {tg_id} добавлен с ролью <b>admin</b>.\n"
        f"Теперь назначь ему магазины — иначе он не увидит ничего.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


# ---------- CARD ----------

@router.callback_query(AdmCb.filter(F.action == "card"))
async def cb_card(call: CallbackQuery, callback_data: AdmCb) -> None:
    admins = await list_admins()
    a = next((x for x in admins if x.tg_id == callback_data.tg_id), None)
    if not a:
        await call.answer("Не найден", show_alert=True)
        return
    shop_ids = await assigned_shop_ids(a.tg_id)
    all_shops = {s.id: s for s in await list_all_shops()}
    names = [all_shops[i].name for i in shop_ids if i in all_shops]
    text = (
        f"👤 <b>{a.tg_id}</b>\n"
        f"Роль: <b>{a.role}</b>\n"
        f"Добавил: {a.added_by or '—'} ({a.added_at.strftime('%d.%m.%Y')})\n"
        f"Заметка: {html.escape(a.note) if a.note else '—'}\n"
        f"Магазинов: {len(shop_ids)}\n"
    )
    if names:
        text += "  • " + "\n  • ".join(html.escape(n) for n in names[:20])
        if len(names) > 20:
            text += f"\n  …и ещё {len(names) - 20}"
    rows = [
        [InlineKeyboardButton(text="📌 Магазины", callback_data=AdmCb(action="assign", tg_id=a.tg_id).pack())],
        [
            InlineKeyboardButton(text="🔁 Роль", callback_data=AdmCb(action="role", tg_id=a.tg_id).pack()),
            InlineKeyboardButton(text="✏️ Заметка", callback_data=AdmCb(action="note", tg_id=a.tg_id).pack()),
        ],
        [InlineKeyboardButton(text="🔍 Открыть как этот админ",
                              callback_data=AdmCb(action="viewas", tg_id=a.tg_id).pack())],
        [InlineKeyboardButton(text="🗑 Снять права", callback_data=AdmCb(action="remove", tg_id=a.tg_id).pack())],
        [InlineKeyboardButton(text="← К списку", callback_data=AdmCb(action="list").pack())],
    ]
    await safe_edit(call, text, InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


# ---------- ASSIGN SHOPS ----------

@router.callback_query(AdmCb.filter(F.action == "assign"))
async def cb_assign(call: CallbackQuery, callback_data: AdmCb, state: FSMContext) -> None:
    current = set(await assigned_shop_ids(callback_data.tg_id))
    # сохраняем текущий выбор в FSM, чтобы тогглить локально до Save
    selected: set[int] = current.copy()
    await state.update_data(target=callback_data.tg_id, selected=list(selected))
    await _render_assign(call, callback_data.tg_id, selected, callback_data.page)


async def _render_assign(call: CallbackQuery, tg_id: int, selected: set[int], page: int = 0) -> None:
    PAGE = 12
    shops = await list_all_shops()
    pages = max(1, (len(shops) + PAGE - 1) // PAGE)
    page = max(0, min(page, pages - 1))
    chunk = shops[page * PAGE:(page + 1) * PAGE]
    rows = []
    for s in chunk:
        mark = "✅" if s.id in selected else "▫️"
        chain = f"[{s.chain_name}] " if s.chain_name else ""
        rows.append([InlineKeyboardButton(
            text=f"{mark} {chain}{s.name}",
            callback_data=AdmCb(action="toggle", tg_id=tg_id, shop_id=s.id, page=page).pack(),
        )])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=AdmCb(action="navp", tg_id=tg_id, page=page - 1).pack()))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=AdmCb(action="navp", tg_id=tg_id, page=page + 1).pack()))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="💾 Сохранить", callback_data=AdmCb(action="save", tg_id=tg_id).pack())])
    rows.append([InlineKeyboardButton(text="← Назад", callback_data=AdmCb(action="card", tg_id=tg_id).pack())])
    await safe_edit(
        call,
        f"📌 Выбери магазины для админа <code>{tg_id}</code>\nВыбрано: {len(selected)}",
        InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await call.answer()


@router.callback_query(AdmCb.filter(F.action == "toggle"))
async def cb_toggle(call: CallbackQuery, callback_data: AdmCb, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("target") != callback_data.tg_id:
        await call.answer("Сессия выбора истекла, открой ещё раз", show_alert=True)
        return
    sel = set(data.get("selected", []))
    if callback_data.shop_id in sel:
        sel.remove(callback_data.shop_id)
    else:
        sel.add(callback_data.shop_id)
    await state.update_data(selected=list(sel))
    await _render_assign(call, callback_data.tg_id, sel, callback_data.page)


@router.callback_query(AdmCb.filter(F.action == "navp"))
async def cb_navp(call: CallbackQuery, callback_data: AdmCb, state: FSMContext) -> None:
    data = await state.get_data()
    sel = set(data.get("selected", []))
    await _render_assign(call, callback_data.tg_id, sel, callback_data.page)


@router.callback_query(AdmCb.filter(F.action == "save"))
async def cb_save(call: CallbackQuery, callback_data: AdmCb, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("target") != callback_data.tg_id:
        await call.answer("Сессия истекла", show_alert=True)
        return
    sel = list(data.get("selected", []))
    await set_admin_assignments(callback_data.tg_id, sel, call.from_user.id)
    await audit_write(call.from_user.id, "admin.assign_shops", "admin", callback_data.tg_id, {"shop_ids": sel})
    await state.clear()
    await call.answer("Сохранено", show_alert=False)
    fake = AdmCb(action="card", tg_id=callback_data.tg_id)
    await cb_card(call, fake)


# ---------- VIEW AS ----------

@router.callback_query(AdmCb.filter(F.action == "viewas"))
async def cb_view_as(call: CallbackQuery, callback_data: AdmCb) -> None:
    """Read-only preview of what this admin sees in his scope."""
    admins = await list_admins()
    a = next((x for x in admins if x.tg_id == callback_data.tg_id), None)
    if not a:
        await call.answer("Не найден", show_alert=True)
        return
    shop_ids = await assigned_shop_ids(a.tg_id)
    all_shops = {s.id: s for s in await list_all_shops()}
    visible = [all_shops[i] for i in shop_ids if i in all_shops]
    role_label = "🛡 super_admin (видит всё)" if a.role == "super_admin" else "👤 admin"

    if a.role == "super_admin":
        scope_lines = [
            "🔍 <b>Просмотр от лица:</b> "
            f"<code>{a.tg_id}</code>",
            f"Роль: {role_label}",
            "",
            "Этот пользователь видит ВСЕ магазины и разделы.",
        ]
    else:
        scope_lines = [
            "🔍 <b>Просмотр от лица:</b> "
            f"<code>{a.tg_id}</code>",
            f"Роль: {role_label}",
            f"Назначено магазинов: {len(visible)}",
            "",
            "<b>Магазины (видимы в админке):</b>",
        ]
        if not visible:
            scope_lines.append("  (пусто — этот админ ничего не увидит)")
        else:
            for s in visible[:30]:
                chain = f"[{s.chain_name}] " if s.chain_name else ""
                flag = "" if s.is_active else " 🚫"
                scope_lines.append(f"  • {chain}{html.escape(s.name)}{flag}")
            if len(visible) > 30:
                scope_lines.append(f"  …и ещё {len(visible) - 30}")
        scope_lines += [
            "",
            "<b>Доступ в разделах</b> (по магазинам выше):",
            "  • Магазины — только эти",
            "  • Завозы и парсеры — только по этим магазинам",
            "  • Пользователи — подписанные на эти магазины",
            "  • Рассылки — только по этим подписчикам",
            "  • Фидбек — с привязкой к этим магазинам",
        ]

    rows = [
        [InlineKeyboardButton(
            text="← Назад к админу",
            callback_data=AdmCb(action="card", tg_id=a.tg_id).pack(),
        )],
    ]
    await safe_edit(call, "\n".join(scope_lines), InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


# ---------- ROLE ----------

@router.callback_query(AdmCb.filter(F.action == "role"))
async def cb_role(call: CallbackQuery, callback_data: AdmCb) -> None:
    rows = [
        [InlineKeyboardButton(text="👤 admin", callback_data=AdmCb(action="setrole", tg_id=callback_data.tg_id, page=0).pack())],
        [InlineKeyboardButton(text="🛡 super_admin", callback_data=AdmCb(action="setrole", tg_id=callback_data.tg_id, page=1).pack())],
        [InlineKeyboardButton(text="← Назад", callback_data=AdmCb(action="card", tg_id=callback_data.tg_id).pack())],
    ]
    await safe_edit(call, f"Выбери роль для {callback_data.tg_id}:", InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


@router.callback_query(AdmCb.filter(F.action == "setrole"))
async def cb_setrole(call: CallbackQuery, callback_data: AdmCb) -> None:
    role = "super_admin" if callback_data.page == 1 else "admin"
    if call.from_user.id == callback_data.tg_id and role != "super_admin":
        await call.answer("Нельзя понизить самого себя", show_alert=True)
        return
    await set_role(callback_data.tg_id, role)
    await audit_write(call.from_user.id, "admin.set_role", "admin", callback_data.tg_id, {"role": role})
    await call.answer("Роль обновлена", show_alert=False)
    fake = AdmCb(action="card", tg_id=callback_data.tg_id)
    await cb_card(call, fake)


# ---------- NOTE ----------

@router.callback_query(AdmCb.filter(F.action == "note"))
async def cb_note_start(call: CallbackQuery, callback_data: AdmCb, state: FSMContext) -> None:
    await state.set_state(AdmStates.note)
    await state.update_data(target=callback_data.tg_id)
    await call.message.answer("Введи заметку (или /clear чтобы стереть, /cancel для отмены):")
    await call.answer()


@router.message(AdmStates.note, F.text == "/cancel")
async def msg_note_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено.")


@router.message(AdmStates.note, F.text)
async def msg_note(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    target = data.get("target")
    raw = message.text.strip()
    note = None if raw == "/clear" else raw[:500]
    await set_note(target, note)
    await audit_write(message.from_user.id, "admin.set_note", "admin", target)
    await state.clear()
    await message.answer("✅ Заметка обновлена.")


# ---------- REMOVE ----------

@router.callback_query(AdmCb.filter(F.action == "remove"))
async def cb_remove_ask(call: CallbackQuery, callback_data: AdmCb) -> None:
    if call.from_user.id == callback_data.tg_id:
        await call.answer("Нельзя снять права с самого себя", show_alert=True)
        return
    rows = [
        [InlineKeyboardButton(
            text="🗑 Точно снять",
            callback_data=AdmCb(action="removec", tg_id=callback_data.tg_id).pack(),
        )],
        [InlineKeyboardButton(text="✖️ Отмена", callback_data=AdmCb(action="card", tg_id=callback_data.tg_id).pack())],
    ]
    await safe_edit(
        call, f"Снять права с <code>{callback_data.tg_id}</code>?\nВсе назначения магазинов будут удалены.",
        InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await call.answer()


@router.callback_query(AdmCb.filter(F.action == "removec"))
async def cb_remove_confirm(call: CallbackQuery, callback_data: AdmCb) -> None:
    if call.from_user.id == callback_data.tg_id:
        await call.answer("Нельзя снять с себя", show_alert=True)
        return
    ok = await remove_admin(callback_data.tg_id)
    if ok:
        await audit_write(call.from_user.id, "admin.remove", "admin", callback_data.tg_id)
    await call.answer("Удалён" if ok else "Не нашёл", show_alert=False)
    fake = AdmCb(action="list")
    await cb_list(call, fake)


# ---------- ASSIGN ADMINS BY SHOP (sa:assignshop:{shop_id}) ----------

async def _render_shop_assign(call: CallbackQuery, shop_id: int, selected: set[int]) -> None:
    shop = await get_shop(shop_id)
    if not shop:
        await call.answer("Магазин не найден", show_alert=True)
        return
    admins = [a for a in await list_admins() if a.role == "admin"]
    rows = []
    for a in admins:
        mark = "✅" if a.tg_id in selected else "▫️"
        label = f"{mark} {a.tg_id}"
        if a.note:
            label += f" • {html.escape(a.note)[:30]}"
        rows.append([InlineKeyboardButton(
            text=label[:60],
            callback_data=f"sa:asnshp:{shop_id}:tog:{a.tg_id}",
        )])
    rows.append([InlineKeyboardButton(
        text="💾 Сохранить",
        callback_data=f"sa:asnshp:{shop_id}:save",
    )])
    rows.append([InlineKeyboardButton(
        text="← К магазину",
        callback_data=f"sa:asnshp:{shop_id}:back",
    )])
    text = (
        f"👥 <b>Админы магазина</b>\n"
        f"🛍 {html.escape(shop.name)} (id={shop.id})\n\n"
        f"Отмечай галочкой кому давать доступ. Сохрани, чтобы применить.\n"
        f"Отмечено: {len([a for a in admins if a.tg_id in selected])}"
    )
    await safe_edit(call, text, InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("sa:assignshop:"))
async def cb_shop_assign_open(call: CallbackQuery, state: FSMContext) -> None:
    try:
        shop_id = int(call.data.rsplit(":", 1)[-1])
    except ValueError:
        await call.answer("Битый callback", show_alert=True)
        return
    current = set(await shop_admins(shop_id))
    await state.update_data(shop_target=shop_id, shop_selected=list(current))
    await _render_shop_assign(call, shop_id, current)
    await call.answer()


@router.callback_query(F.data.startswith("sa:asnshp:"))
async def cb_shop_assign_action(call: CallbackQuery, state: FSMContext) -> None:
    parts = call.data.split(":")
    # sa:asnshp:{shop_id}:{op}[:{admin_id}]
    if len(parts) < 4:
        await call.answer("Битый callback", show_alert=True)
        return
    try:
        shop_id = int(parts[2])
    except ValueError:
        await call.answer("Битый callback", show_alert=True)
        return
    op = parts[3]
    data = await state.get_data()
    if data.get("shop_target") != shop_id:
        await call.answer("Сессия истекла, открой ещё раз", show_alert=True)
        return
    selected = set(data.get("shop_selected", []))

    if op == "tog" and len(parts) >= 5:
        try:
            admin_id = int(parts[4])
        except ValueError:
            await call.answer("Битый callback", show_alert=True)
            return
        if admin_id in selected:
            selected.remove(admin_id)
        else:
            selected.add(admin_id)
        await state.update_data(shop_selected=list(selected))
        await _render_shop_assign(call, shop_id, selected)
        await call.answer()
        return

    if op == "save":
        before = set(await shop_admins(shop_id))
        to_add = selected - before
        to_remove = before - selected
        for tg in to_add:
            await assign_shop(shop_id, tg, call.from_user.id)
        for tg in to_remove:
            await unassign_shop(shop_id, tg)
        await audit_write(
            call.from_user.id, "shop.assign_admins", "shop", shop_id,
            {"added": list(to_add), "removed": list(to_remove)},
        )
        await state.update_data(shop_target=None, shop_selected=[])
        await call.answer(f"Сохранено: +{len(to_add)} / -{len(to_remove)}", show_alert=True)
        # вернёмся в карточку магазина
        from handlers.admin.shops import ShopCb, _show_card  # type: ignore
        try:
            await _show_card(call, shop_id, 0)
        except Exception:
            log.exception("show card after shop-assign failed")
        return

    if op == "back":
        await state.update_data(shop_target=None, shop_selected=[])
        from handlers.admin.shops import _show_card  # type: ignore
        try:
            await _show_card(call, shop_id, 0)
        except Exception:
            log.exception("show card after shop-assign back failed")
        await call.answer()
        return

    await call.answer("Неизвестное действие", show_alert=True)
