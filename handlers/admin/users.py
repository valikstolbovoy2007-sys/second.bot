import html
import logging
from datetime import date, datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from data.db import pool
from data.repos.admin_roles import is_super_admin, visible_shop_ids
from handlers.admin.filters import IsAdmin
from handlers.admin.ui import safe_edit
from services.audit import write as audit_write
from states.admin_states import AdminDmStates

log = logging.getLogger(__name__)
router = Router(name="admin_users")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


class UCb(CallbackData, prefix="admu"):
    action: str
    tg_id: int = 0
    value: str | None = None


class USearch(StatesGroup):
    query = State()


@router.callback_query(F.data == "adm:users:menu")
async def cb_menu(call: CallbackQuery) -> None:
    rows = [
        [InlineKeyboardButton(text="🔍 Найти пользователя", callback_data=UCb(action="search").pack())],
        [InlineKeyboardButton(text="📋 Список", callback_data=UCb(action="list").pack())],
    ]
    if await is_super_admin(call.from_user.id):
        rows.append([InlineKeyboardButton(text="🚫 Заблокированные", callback_data=UCb(action="blocks").pack())])
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="adm:back")])
    await safe_edit(call, "👥 <b>Пользователи</b>", InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


async def _scoped_users(actor_tg_id: int, search: str | None, limit: int = 30) -> list[dict]:
    scope = await visible_shop_ids(actor_tg_id)
    where = []
    args: list = []
    i = 1
    if scope is not None:
        if not scope:
            return []
        where.append(f"u.id IN (SELECT user_id FROM subscriptions WHERE shop_id = ANY(${i}))")
        args.append(scope)
        i += 1
    if search:
        if search.lstrip("-").isdigit():
            where.append(f"u.tg_id = ${i}")
            args.append(int(search))
        else:
            where.append(f"(u.username ILIKE ${i})")
            args.append(f"%{search.lstrip('@')}%")
        i += 1
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    args.append(limit)
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT u.tg_id, u.username, u.created_at, u.pause_until, u.is_blocked,
                   (SELECT count(*) FROM subscriptions WHERE user_id = u.id) AS subs
            FROM users u
            {where_sql}
            ORDER BY u.created_at DESC
            LIMIT ${i}
            """,
            *args,
        )
    return [dict(r) for r in rows]


@router.callback_query(UCb.filter(F.action == "list"))
async def cb_list(call: CallbackQuery) -> None:
    items = await _scoped_users(call.from_user.id, None, 30)
    if not items:
        await safe_edit(
            call, "Пользователей не найдено.",
            InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="← Назад", callback_data="adm:users:menu")]]),
        )
        await call.answer()
        return
    rows = [
        [InlineKeyboardButton(
            text=f"@{u['username'] or u['tg_id']} • подп: {u['subs']}",
            callback_data=UCb(action="card", tg_id=u['tg_id']).pack(),
        )]
        for u in items
    ]
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="adm:users:menu")])
    await safe_edit(call, f"<b>Пользователи ({len(items)})</b>", InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


@router.callback_query(UCb.filter(F.action == "search"))
async def cb_search(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(USearch.query)
    await call.message.answer("Введи tg_id или @username:")
    await call.answer()


@router.message(USearch.query, F.text)
async def msg_search(message: Message, state: FSMContext) -> None:
    items = await _scoped_users(message.from_user.id, message.text.strip(), 30)
    await state.clear()
    if not items:
        await message.answer("Не найдено в твоём scope.")
        return
    lines = ["<b>Найдено:</b>"]
    rows = []
    for u in items:
        lines.append(f"• @{u['username'] or '—'} ({u['tg_id']}) — подписок: {u['subs']}")
        rows.append([InlineKeyboardButton(
            text=f"@{u['username'] or u['tg_id']}",
            callback_data=UCb(action="card", tg_id=u['tg_id']).pack(),
        )])
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="adm:users:menu")])
    await message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


def _card_kb(tg_id: int, is_super: bool, paused: bool, blocked: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="✉️ Написать ЛС", callback_data=UCb(action="dm", tg_id=tg_id).pack())],
    ]
    if paused:
        rows.append([InlineKeyboardButton(
            text="▶️ Снять паузу",
            callback_data=UCb(action="unpause", tg_id=tg_id).pack(),
        )])
    else:
        rows.append([InlineKeyboardButton(
            text="⏸ Поставить на паузу 7д",
            callback_data=UCb(action="pause", tg_id=tg_id, value="7").pack(),
        )])
    if is_super:
        if blocked:
            rows.append([InlineKeyboardButton(
                text="✅ Разблокировать",
                callback_data=UCb(action="unblock", tg_id=tg_id).pack(),
            )])
        else:
            rows.append([InlineKeyboardButton(
                text="🚫 Заблокировать в боте",
                callback_data=UCb(action="block", tg_id=tg_id).pack(),
            )])
    rows.append([InlineKeyboardButton(text="← Назад", callback_data=UCb(action="list").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _user_status(tg_id: int) -> tuple[bool, bool]:
    """Returns (paused, blocked)."""
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT pause_until, EXISTS(SELECT 1 FROM user_blocks WHERE tg_id = $1) AS blocked "
            "FROM users WHERE tg_id = $1",
            tg_id,
        )
    if not row:
        return False, False
    pu = row["pause_until"]
    paused = bool(pu and pu > date.today())
    return paused, bool(row["blocked"])


@router.callback_query(UCb.filter(F.action == "card"))
async def cb_card(call: CallbackQuery, callback_data: UCb) -> None:
    items = await _scoped_users(call.from_user.id, str(callback_data.tg_id), 1)
    if not items:
        await call.answer("Нет доступа", show_alert=True)
        return
    u = items[0]
    paused, blocked = await _user_status(u["tg_id"])
    is_super = await is_super_admin(call.from_user.id)
    pause_label = u["pause_until"].strftime("%d.%m.%Y") if u["pause_until"] else "—"
    text = (
        f"👤 <b>@{html.escape(u['username'] or '—')}</b> ({u['tg_id']})\n"
        f"Регистрация: {u['created_at'].strftime('%d.%m.%Y')}\n"
        f"Подписок: {u['subs']}\n"
        f"Пауза до: {pause_label}\n"
        f"Заблокирован: {'да' if blocked else 'нет'}"
    )
    await safe_edit(call, text, _card_kb(u["tg_id"], is_super, paused, blocked))
    await call.answer()


# ---------- DM ----------

@router.callback_query(UCb.filter(F.action == "dm"))
async def cb_dm_start(call: CallbackQuery, callback_data: UCb, state: FSMContext) -> None:
    items = await _scoped_users(call.from_user.id, str(callback_data.tg_id), 1)
    if not items:
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminDmStates.text)
    await state.update_data(target_tg_id=callback_data.tg_id)
    await call.message.answer(
        f"✉️ Введи текст сообщения для @{html.escape(items[0]['username'] or str(callback_data.tg_id))} "
        f"(до 4000 символов). /cancel — отмена."
    )
    await call.answer()


@router.message(AdminDmStates.text, F.text)
async def msg_dm_send(message: Message, state: FSMContext, bot: Bot) -> None:
    if message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("Отменено.")
        return
    text = message.text.strip()
    if len(text) > 4000:
        await message.answer("Слишком длинно (макс 4000). Сократи.")
        return
    data = await state.get_data()
    target = int(data.get("target_tg_id", 0))
    if not target:
        await state.clear()
        await message.answer("Битый адресат. Начни заново.")
        return
    body = (
        "✉️ <b>Сообщение от администрации</b>\n\n"
        f"{html.escape(text)}\n\n"
        "<i>Ответить можно через /feedback.</i>"
    )
    try:
        await bot.send_message(target, body)
    except Exception as e:
        log.exception("admin DM failed")
        await message.answer(f"❌ Не доставлено: {html.escape(str(e))}")
        await state.clear()
        return
    async with pool().acquire() as conn:
        await conn.execute(
            "INSERT INTO admin_messages (from_tg_id, to_tg_id, text) VALUES ($1,$2,$3)",
            message.from_user.id, target, text,
        )
    await audit_write(
        message.from_user.id, "user.dm", "user", target,
        {"len": len(text)},
    )
    await state.clear()
    await message.answer("✅ Доставлено.")


# ---------- pause / unpause ----------

@router.callback_query(UCb.filter(F.action == "pause"))
async def cb_pause(call: CallbackQuery, callback_data: UCb) -> None:
    items = await _scoped_users(call.from_user.id, str(callback_data.tg_id), 1)
    if not items:
        await call.answer("Нет доступа", show_alert=True)
        return
    days = 7
    try:
        days = int(callback_data.value or "7")
    except ValueError:
        pass
    until = date.today() + timedelta(days=days)
    async with pool().acquire() as conn:
        await conn.execute(
            "UPDATE users SET pause_until = $1 WHERE tg_id = $2",
            until, callback_data.tg_id,
        )
    await audit_write(
        call.from_user.id, "user.pause", "user", callback_data.tg_id,
        {"until": until.isoformat()},
    )
    await call.answer(f"Пауза до {until.strftime('%d.%m.%Y')}")
    callback_data_view = UCb(action="card", tg_id=callback_data.tg_id)
    call.data = callback_data_view.pack()
    await cb_card(call, callback_data_view)


@router.callback_query(UCb.filter(F.action == "unpause"))
async def cb_unpause(call: CallbackQuery, callback_data: UCb) -> None:
    items = await _scoped_users(call.from_user.id, str(callback_data.tg_id), 1)
    if not items:
        await call.answer("Нет доступа", show_alert=True)
        return
    async with pool().acquire() as conn:
        await conn.execute(
            "UPDATE users SET pause_until = NULL WHERE tg_id = $1",
            callback_data.tg_id,
        )
    await audit_write(call.from_user.id, "user.unpause", "user", callback_data.tg_id)
    await call.answer("Пауза снята")
    callback_data_view = UCb(action="card", tg_id=callback_data.tg_id)
    call.data = callback_data_view.pack()
    await cb_card(call, callback_data_view)


# ---------- block / unblock (super only) ----------

@router.callback_query(UCb.filter(F.action == "block"))
async def cb_block(call: CallbackQuery, callback_data: UCb) -> None:
    if not await is_super_admin(call.from_user.id):
        await call.answer("Только супер-админ", show_alert=True)
        return
    async with pool().acquire() as conn:
        await conn.execute(
            "INSERT INTO user_blocks (tg_id, blocked_by) VALUES ($1,$2) ON CONFLICT DO NOTHING",
            callback_data.tg_id, call.from_user.id,
        )
        await conn.execute(
            "UPDATE users SET is_blocked = true WHERE tg_id = $1",
            callback_data.tg_id,
        )
    await audit_write(call.from_user.id, "user.block", "user", callback_data.tg_id)
    await call.answer("Заблокирован", show_alert=True)
    callback_data_view = UCb(action="card", tg_id=callback_data.tg_id)
    call.data = callback_data_view.pack()
    await cb_card(call, callback_data_view)


@router.callback_query(UCb.filter(F.action == "unblock"))
async def cb_unblock(call: CallbackQuery, callback_data: UCb) -> None:
    if not await is_super_admin(call.from_user.id):
        await call.answer("Только супер-админ", show_alert=True)
        return
    async with pool().acquire() as conn:
        await conn.execute("DELETE FROM user_blocks WHERE tg_id = $1", callback_data.tg_id)
        await conn.execute(
            "UPDATE users SET is_blocked = false WHERE tg_id = $1",
            callback_data.tg_id,
        )
    await audit_write(call.from_user.id, "user.unblock", "user", callback_data.tg_id)
    await call.answer("Разблокирован")
    callback_data_view = UCb(action="card", tg_id=callback_data.tg_id)
    call.data = callback_data_view.pack()
    await cb_card(call, callback_data_view)


# ---------- block list (super only) ----------

@router.callback_query(UCb.filter(F.action == "blocks"))
async def cb_blocks(call: CallbackQuery) -> None:
    if not await is_super_admin(call.from_user.id):
        await call.answer("Только супер-админ", show_alert=True)
        return
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT b.tg_id, b.reason, b.blocked_at, b.blocked_by, u.username
            FROM user_blocks b
            LEFT JOIN users u ON u.tg_id = b.tg_id
            ORDER BY b.blocked_at DESC
            LIMIT 50
            """
        )
    if not rows:
        await safe_edit(
            call, "Список блокировок пуст.",
            InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="← Назад", callback_data="adm:users:menu")]]),
        )
        await call.answer()
        return
    lines = [f"🚫 <b>Заблокировано: {len(rows)}</b>", ""]
    kb_rows: list[list[InlineKeyboardButton]] = []
    for r in rows:
        ts = r["blocked_at"].strftime("%d.%m %H:%M")
        uname = r["username"] or "—"
        lines.append(f"• @{html.escape(uname)} ({r['tg_id']}) — {ts}")
        kb_rows.append([InlineKeyboardButton(
            text=f"@{uname} ({r['tg_id']})",
            callback_data=UCb(action="card", tg_id=int(r["tg_id"])).pack(),
        )])
    kb_rows.append([InlineKeyboardButton(text="← Назад", callback_data="adm:users:menu")])
    await safe_edit(call, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await call.answer()
