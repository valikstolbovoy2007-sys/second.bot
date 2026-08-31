import html
import logging

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
from data.repos.admin_roles import visible_shop_ids
from handlers.admin.filters import IsAdmin
from handlers.admin.ui import safe_edit
from services.audit import write as audit_write

log = logging.getLogger(__name__)
router = Router(name="admin_feedback")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

PAGE_SIZE = 8


class FbCb(CallbackData, prefix="admfb"):
    action: str
    fb_id: int = 0
    page: int = 0


class FbStates(StatesGroup):
    reply = State()


async def _scope_clause(actor_tg_id: int):
    """Returns (where_sql, args). Empty list of args = no scope filter."""
    scope = await visible_shop_ids(actor_tg_id)
    if scope is None:
        return "", []
    if not scope:
        return "WHERE FALSE", []
    return "WHERE shop_id = ANY($1) OR shop_id IS NULL", [scope]


@router.callback_query(FbCb.filter(F.action == "list"))
async def cb_list(call: CallbackQuery, callback_data: FbCb) -> None:
    page = max(0, callback_data.page)
    where, args = await _scope_clause(call.from_user.id)
    pl = len(args)
    args_full = args + [PAGE_SIZE, page * PAGE_SIZE]
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT f.id, f.text, f.status, f.created_at, f.shop_id,
                   u.tg_id AS user_tg, u.username
            FROM feedback f
            LEFT JOIN users u ON u.id = f.user_id
            {where}
            ORDER BY (f.status = 'closed') ASC, f.created_at DESC
            LIMIT ${pl + 1} OFFSET ${pl + 2}
            """,
            *args_full,
        )
        total = int(await conn.fetchval(
            f"SELECT count(*) FROM feedback {where}", *args
        ) or 0)
    if total == 0:
        await safe_edit(
            call, "💬 Фидбека пока нет.",
            InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="← Назад", callback_data="adm:back")]]),
        )
        await call.answer()
        return

    lines = [f"💬 <b>Фидбек ({total})</b>", ""]
    btn_rows = []
    for r in rows:
        emoji = {"new": "🆕", "in_progress": "🔄", "closed": "✅"}.get(r["status"], "•")
        preview = (r["text"] or "")[:60].replace("\n", " ")
        lines.append(f"{emoji} #{r['id']} от @{r['username'] or r['user_tg'] or '—'}: {html.escape(preview)}")
        btn_rows.append([InlineKeyboardButton(
            text=f"{emoji} #{r['id']}",
            callback_data=FbCb(action="open", fb_id=r["id"], page=page).pack(),
        )])
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="◀️", callback_data=FbCb(action="list", page=page - 1).pack()))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="noop"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton(text="▶️", callback_data=FbCb(action="list", page=page + 1).pack()))
        btn_rows.append(nav)
    btn_rows.append([InlineKeyboardButton(text="← Назад", callback_data="adm:back")])
    await safe_edit(call, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=btn_rows))
    await call.answer()


@router.callback_query(FbCb.filter(F.action == "open"))
async def cb_open(call: CallbackQuery, callback_data: FbCb) -> None:
    where, args = await _scope_clause(call.from_user.id)
    cond = where or "WHERE TRUE"
    pl = len(args)
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            f"""
            SELECT f.id, f.text, f.status, f.created_at, f.shop_id, u.tg_id AS user_tg, u.username
            FROM feedback f LEFT JOIN users u ON u.id = f.user_id
            {cond} {'AND' if where else 'WHERE'} f.id = ${pl + 1}
            """,
            *args, callback_data.fb_id,
        )
    if not row:
        await call.answer("Не найдено в твоём scope", show_alert=True)
        return
    async with pool().acquire() as conn:
        replies = await conn.fetch(
            """
            SELECT from_tg_id, text, sent_at
            FROM admin_messages
            WHERE feedback_id = $1
            ORDER BY sent_at ASC
            LIMIT 20
            """,
            row['id'],
        )
    parts = [
        f"💬 <b>Фидбек #{row['id']}</b>  ({row['status']})",
        f"От: @{row['username'] or '—'} ({row['user_tg']})",
        f"Создано: {row['created_at'].strftime('%d.%m %H:%M')}",
        "────────────────",
        html.escape(row['text']),
    ]
    if replies:
        parts.append("")
        parts.append(f"<b>Ответы ({len(replies)}):</b>")
        for rep in replies:
            ts = rep['sent_at'].strftime('%d.%m %H:%M')
            preview = (rep['text'] or "")[:300]
            parts.append(f"<code>{ts}</code> admin {rep['from_tg_id']}:\n{html.escape(preview)}")
    text = "\n".join(parts)
    rows = [
        [InlineKeyboardButton(text="↩️ Ответить", callback_data=FbCb(action="reply", fb_id=row['id'], page=callback_data.page).pack())],
        [InlineKeyboardButton(text="✅ Закрыть", callback_data=FbCb(action="close", fb_id=row['id'], page=callback_data.page).pack())],
        [InlineKeyboardButton(text="← К списку", callback_data=FbCb(action="list", page=callback_data.page).pack())],
    ]
    await safe_edit(call, text, InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


@router.callback_query(FbCb.filter(F.action == "close"))
async def cb_close(call: CallbackQuery, callback_data: FbCb) -> None:
    async with pool().acquire() as conn:
        await conn.execute("UPDATE feedback SET status='closed' WHERE id=$1", callback_data.fb_id)
    await audit_write(call.from_user.id, "feedback.close", "feedback", callback_data.fb_id)
    await call.answer("Закрыт", show_alert=False)
    callback_data2 = FbCb(action="open", fb_id=callback_data.fb_id, page=callback_data.page)
    await cb_open(call, callback_data2)


@router.callback_query(FbCb.filter(F.action == "reply"))
async def cb_reply(call: CallbackQuery, callback_data: FbCb, state: FSMContext) -> None:
    await state.set_state(FbStates.reply)
    await state.update_data(fb_id=callback_data.fb_id, page=callback_data.page)
    await call.message.answer("Введи ответ пользователю (или /cancel):")
    await call.answer()


@router.message(FbStates.reply, F.text == "/cancel")
async def msg_reply_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено.")


@router.message(FbStates.reply, F.text)
async def msg_reply_send(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    fb_id = data["fb_id"]
    text = message.html_text
    async with pool().acquire() as conn:
        target = await conn.fetchrow(
            "SELECT u.tg_id FROM feedback f JOIN users u ON u.id = f.user_id WHERE f.id = $1",
            fb_id,
        )
    await state.clear()
    if not target:
        await message.answer("Не нашёл пользователя.")
        return
    try:
        await bot.send_message(target["tg_id"], f"💬 Ответ от админа:\n\n{text}")
        async with pool().acquire() as conn:
            await conn.execute(
                "INSERT INTO admin_messages (from_tg_id, to_tg_id, text, feedback_id) VALUES ($1,$2,$3,$4)",
                message.from_user.id, target["tg_id"], text, fb_id,
            )
            await conn.execute("UPDATE feedback SET status='in_progress' WHERE id=$1", fb_id)
        await audit_write(message.from_user.id, "feedback.reply", "feedback", fb_id)
        await message.answer("✅ Отправлено.")
    except Exception as e:
        log.exception("feedback reply failed")
        await message.answer(f"❌ Не отправилось: {html.escape(str(e))}")
