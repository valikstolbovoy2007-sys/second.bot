"""Вопросы сешке — админская часть.

Обычный админ видит и отвечает на вопросы своих магазинов (кнопка
«❓ Вопросы» в панели + уведомления в ЛС). Супер-админ — только просматривает.
"""
import html
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import settings
from data.db import pool
from data.repos.admin_roles import can_access_shop, is_super_admin, visible_shop_ids
from data.repos.questions_repo import add_question_message, set_question_status
from handlers.admin.filters import IsAdmin
from handlers.admin.ui import safe_edit
from services.audit import write as audit_write
from services.chat_journal import journal
from services.chat_render import render

log = logging.getLogger(__name__)
router = Router(name="admin_questions")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

PAGE_SIZE = 8

_ZWJ = "\u200d"
_PREVIEW_PAD = 20


class QCb(CallbackData, prefix="admq"):
    action: str
    q_id: int = 0
    page: int = 0


class QStates(StatesGroup):
    reply = State()
    confirm = State()


def _pad_to_width(text: str, width: int = _PREVIEW_PAD) -> str:
    if len(text) < width:
        text += " " * (width - len(text)) + _ZWJ
    return text


def _reply_preview(text: str) -> str:
    line = _pad_to_width((text or "…").replace("\n", " "))
    return f"💬 <b>Ответ на вопрос:</b>\n<pre>{html.escape(line)}</pre>"


def _reply_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📤 Ответить", callback_data=QCb(action="send").pack()),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=QCb(action="discard").pack()),
        ],
    ])


async def _scope_clause(actor_tg_id: int):
    """WHERE-фрагмент для вопросов: у супер-админа — всё, у админа — свои."""
    scope = await visible_shop_ids(actor_tg_id)
    if scope is None:
        return "", []
    if not scope:
        return "WHERE FALSE", []
    return "WHERE q.shop_id = ANY($1)", [scope]


async def _fetch_q_target(q_id: int):
    async with pool().acquire() as conn:
        return await conn.fetchrow(
            """
            SELECT u.tg_id, u.username, q.text, q.created_at, q.shop_id, s.name AS shop_name
            FROM questions q
            JOIN users u ON u.id = q.user_id
            LEFT JOIN shops s ON s.id = q.shop_id
            WHERE q.id = $1
            """,
            q_id,
        )


async def _open_question(
    bot: Bot, chat_id: int, message_id: int, actor_tg_id: int, q_id: int, page: int,
) -> int | None:
    """Рендер экрана вопроса. None — не найден/вне scope.

    Кнопки «Ответить»/«Закрыть» есть только у обычного админа с доступом
    к магазину; супер-админ видит вопрос без кнопок (только просмотр).
    """
    page = max(0, page)
    where, args = await _scope_clause(actor_tg_id)
    pl = len(args)
    cond = f"{where} AND" if where else "WHERE"
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            f"""
            SELECT q.id, q.text, q.status, q.created_at, q.shop_id,
                   u.tg_id AS user_tg, u.username, s.name AS shop_name
            FROM questions q
            LEFT JOIN users u ON u.id = q.user_id
            LEFT JOIN shops s ON s.id = q.shop_id
            {cond} q.id = ${pl + 1}
            """,
            *args, q_id,
        )
    if not row:
        return None
    async with pool().acquire() as conn:
        replies = await conn.fetch(
            """
            SELECT from_tg_id, text, sent_at
            FROM question_messages
            WHERE question_id = $1
            ORDER BY sent_at ASC
            LIMIT 20
            """,
            row['id'],
        )
    parts = [
        f"❓ <b>Вопрос сешке</b>  ({row['status']})",
        f"Магазин: <b>{html.escape(row['shop_name'] or '—')}</b>",
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
    is_super = await is_super_admin(actor_tg_id)
    rows: list[list[InlineKeyboardButton]] = []
    if not is_super:
        rows.append([
            InlineKeyboardButton(
                text="↩️ Ответить",
                callback_data=QCb(action="reply", q_id=row['id'], page=page).pack(),
            ),
            InlineKeyboardButton(
                text="✅ Закрыть",
                callback_data=QCb(action="close", q_id=row['id'], page=page).pack(),
            ),
        ])
    if chat_id != settings.ADMIN_CHAT_ID:
        rows.append([InlineKeyboardButton(
            text="← К списку", callback_data=QCb(action="list", page=page).pack(),
        )])
    return await render(bot, chat_id, message_id, text, InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(QCb.filter(F.action == "list"))
async def cb_list(call: CallbackQuery, callback_data: QCb) -> None:
    page = max(0, callback_data.page)
    where, args = await _scope_clause(call.from_user.id)
    pl = len(args)
    args_full = args + [PAGE_SIZE, page * PAGE_SIZE]
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT q.id, q.text, q.status, q.created_at, q.shop_id,
                   u.tg_id AS user_tg, u.username, s.name AS shop_name
            FROM questions q
            LEFT JOIN users u ON u.id = q.user_id
            LEFT JOIN shops s ON s.id = q.shop_id
            {where}
            ORDER BY (q.status = 'closed') ASC, q.created_at DESC
            LIMIT ${pl + 1} OFFSET ${pl + 2}
            """,
            *args_full,
        )
        total = int(await conn.fetchval(
            f"SELECT count(*) FROM questions q {where}", *args
        ) or 0)
    if total == 0:
        await safe_edit(
            call, "❓ Вопросов сешкам пока нет.",
            InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="← Назад", callback_data="adm:back")],
            ]),
        )
        await call.answer()
        return

    lines = [f"❓ <b>Вопросы сешкам ({total})</b>", ""]
    btn_rows = []
    for r in rows:
        emoji = {"new": "🆕", "in_progress": "🔄", "closed": "✅"}.get(r["status"], "•")
        preview = (r["text"] or "")[:60].replace("\n", " ")
        shop = html.escape(r["shop_name"] or "—")
        lines.append(f"{emoji} #{r['id']} [{shop}] от @{r['username'] or r['user_tg'] or '—'}: {html.escape(preview)}")
        btn_rows.append([InlineKeyboardButton(
            text=f"{emoji} #{r['id']} {r['shop_name'] or ''}"[:60],
            callback_data=QCb(action="open", q_id=r["id"], page=page).pack(),
        )])
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="◀️", callback_data=QCb(action="list", page=page - 1).pack()))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="noop"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton(text="▶️", callback_data=QCb(action="list", page=page + 1).pack()))
        btn_rows.append(nav)
    btn_rows.append([InlineKeyboardButton(text="← Назад", callback_data="adm:back")])
    await safe_edit(call, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=btn_rows))
    await call.answer()


@router.callback_query(QCb.filter(F.action == "open"))
async def cb_open(call: CallbackQuery, callback_data: QCb) -> None:
    new_id = await _open_question(
        call.bot, call.message.chat.id, call.message.message_id,
        call.from_user.id, callback_data.q_id, callback_data.page,
    )
    if new_id is None:
        await call.answer("Не найдено в твоём scope", show_alert=True)
        return
    await call.answer()


@router.callback_query(QCb.filter(F.action == "close"))
async def cb_close(call: CallbackQuery, callback_data: QCb, state: FSMContext) -> None:
    if await is_super_admin(call.from_user.id):
        await call.answer("👁 Просмотр — без изменений", show_alert=True)
        return
    if not await can_access_shop(call.from_user.id, (await _fetch_q_target(callback_data.q_id) or {}).get("shop_id", 0)):
        await call.answer("Не найдено в твоём scope", show_alert=True)
        return
    async with pool().acquire() as conn:
        await conn.execute("UPDATE questions SET status='closed' WHERE id=$1", callback_data.q_id)
    await audit_write(call.from_user.id, "question.close", "question", callback_data.q_id)
    await state.clear()
    await call.answer("Закрыт", show_alert=False)
    if call.message.chat.id == settings.ADMIN_CHAT_ID:
        try:
            await call.bot.delete_message(call.message.chat.id, call.message.message_id)
        except TelegramBadRequest:
            await cb_open(call, QCb(action="open", q_id=callback_data.q_id, page=callback_data.page))
        return
    await cb_open(call, QCb(action="open", q_id=callback_data.q_id, page=callback_data.page))


@router.callback_query(QCb.filter(F.action == "reply"))
async def cb_reply(call: CallbackQuery, callback_data: QCb, state: FSMContext) -> None:
    if await is_super_admin(call.from_user.id):
        await call.answer("👁 Просмотр — только для админа сешки", show_alert=True)
        return
    target = await _fetch_q_target(callback_data.q_id)
    if not target or not await can_access_shop(call.from_user.id, target["shop_id"]):
        await call.answer("Не найдено в твоём scope", show_alert=True)
        return
    await state.set_state(QStates.reply)
    await state.update_data(q_id=callback_data.q_id, page=callback_data.page)
    new_id = await safe_edit(call, "✉️ Введи ответ пользователю (или /cancel):")
    await state.update_data(prompt_id=new_id)
    await call.answer()


@router.message(QStates.reply, F.text == "/cancel")
async def msg_reply_cancel(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    try:
        await message.delete()
    except TelegramBadRequest:
        pass
    if data.get("prompt_id") is not None:
        await _open_question(
            message.bot, message.chat.id, data["prompt_id"],
            message.from_user.id, data.get("q_id", 0), data.get("page", 0),
        )


@router.message(QStates.reply, F.text)
async def msg_reply_send(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    data = await state.get_data()
    prompt_id = data.get("prompt_id")
    if not raw:
        try:
            await render(
                message.bot, message.chat.id, prompt_id,
                "⚠️ Ответ не может быть пустым. Введи текст.",
            )
        except Exception:
            pass
        return
    try:
        await message.delete()
    except TelegramBadRequest:
        pass
    reply_html = message.html_text or ""
    await state.update_data(reply_text=reply_html)
    await state.set_state(QStates.confirm)
    if prompt_id is not None:
        await render(
            message.bot, message.chat.id, prompt_id,
            _reply_preview(raw), _reply_confirm_kb(),
        )
    else:
        await message.answer(_reply_preview(raw), reply_markup=_reply_confirm_kb())


@router.message(QStates.confirm, Command("cancel"))
async def msg_confirm_cancel(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    try:
        await message.delete()
    except TelegramBadRequest:
        pass
    if data.get("prompt_id") is not None:
        await _open_question(
            message.bot, message.chat.id, data["prompt_id"],
            message.from_user.id, data.get("q_id", 0), data.get("page", 0),
        )


async def _notify_admin_chat(bot: Bot, target, q_id: int, reply_text: str) -> None:
    """Эхо ответа админа в админ-чат — для просмотра супер-админом
    (без кнопок действий). Ошибка некритична."""
    if not settings.ADMIN_CHAT_ID:
        return
    try:
        username = target.get("username")
        uname = f"@{username}" if username else f"id={target['tg_id']}"
        text = (
            f"✍️ <b>Ответ админа на вопрос #{q_id}</b> "
            f"сешке «{html.escape(target['shop_name'] or '—')}» от {html.escape(uname)}\n\n"
            f"<b>Вопрос:</b>\n{html.escape(target['text'] or '')}\n\n"
            f"<b>Ответ:</b>\n{reply_text}"
        )
        sent = await bot.send_message(
            settings.ADMIN_CHAT_ID, text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text="👁 Открыть",
                    callback_data=QCb(action="open", q_id=q_id, page=0).pack(),
                ),
            ]]),
        )
        journal.record(settings.ADMIN_CHAT_ID, sent.message_id)
    except Exception:
        log.exception("failed to echo question reply to admin chat")


@router.callback_query(QCb.filter(F.action == "send"))
async def cb_reply_send(call: CallbackQuery, callback_data: QCb, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    reply_text = data.get("reply_text") or ""
    q_id = data.get("q_id") or callback_data.q_id
    page = data.get("page") or callback_data.page
    target = await _fetch_q_target(q_id)
    if not target:
        await state.clear()
        await call.answer("Не нашёл пользователя", show_alert=True)
        await _open_question(
            call.bot, call.message.chat.id, call.message.message_id,
            call.from_user.id, q_id, page,
        )
        return
    parts = ["💬 <b>Ответ на твой вопрос</b>"]
    parts.append(f"🛍 Магазин: <b>{html.escape(target['shop_name'] or '—')}</b>")
    parts.append(f"📅 {target['created_at'].strftime('%d.%m.%Y %H:%M')}")
    parts.append("")
    parts.append(html.escape(target["text"] or ""))
    parts.append("")
    parts.append("────────────────")
    parts.append("")
    parts.append("💬 <b>Ответ админа:</b>")
    parts.append("")
    parts.append(reply_text)
    if target["tg_id"] != call.from_user.id:
        try:
            sent = await bot.send_message(target["tg_id"], "\n".join(parts))
            journal.record(target["tg_id"], sent.message_id)
        except Exception as exc:
            log.exception("question reply failed")
            await state.set_state(QStates.confirm)
            await safe_edit(call, _reply_preview(reply_text), _reply_confirm_kb())
            await call.answer(f"❌ Не отправилось: {html.escape(str(exc))[:80]}", show_alert=True)
            return
    await _notify_admin_chat(bot, target, q_id, reply_text)
    await add_question_message(q_id, call.from_user.id, target["tg_id"], reply_text)
    await set_question_status(q_id, "in_progress")
    await audit_write(call.from_user.id, "question.reply", "question", q_id)
    await state.clear()
    await call.answer("✅ Отправлено")
    if call.message.chat.id == settings.ADMIN_CHAT_ID:
        try:
            await call.bot.delete_message(call.message.chat.id, call.message.message_id)
        except TelegramBadRequest:
            await _open_question(
                call.bot, call.message.chat.id, call.message.message_id,
                call.from_user.id, q_id, page,
            )
    else:
        await _open_question(
            call.bot, call.message.chat.id, call.message.message_id,
            call.from_user.id, q_id, page,
        )


@router.callback_query(QCb.filter(F.action == "discard"))
async def cb_reply_discard(call: CallbackQuery, callback_data: QCb, state: FSMContext) -> None:
    data = await state.get_data()
    q_id = data.get("q_id") or callback_data.q_id
    page = data.get("page") or callback_data.page
    await state.clear()
    await call.answer("🗑 Удалено")
    await _open_question(
        call.bot, call.message.chat.id, call.message.message_id,
        call.from_user.id, q_id, page,
    )
