"""Admin stats (TZ §9).

Quick overview, time-based KPIs (DAU/WAU/MAU, growth, notifications),
top-N shop list, and CSV export.
"""
import csv
import html
import io
import logging
from datetime import date, timedelta

from aiogram import F, Router
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from data.db import pool
from data.repos.admin_roles import visible_shop_ids
from handlers.admin.filters import IsAdmin
from handlers.admin.ui import safe_edit

log = logging.getLogger(__name__)
router = Router(name="admin_stats")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def _menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Обзор", callback_data="adm:st:overview")],
        [InlineKeyboardButton(text="🔝 Топ магазинов", callback_data="adm:st:top")],
        [InlineKeyboardButton(text="📩 Уведомления", callback_data="adm:st:notif")],
        [InlineKeyboardButton(text="⬇️ CSV экспорт", callback_data="adm:st:csvmenu")],
        [InlineKeyboardButton(text="← Назад", callback_data="adm:back")],
    ])


@router.callback_query(F.data == "adm:stats")
async def cb_stats(call: CallbackQuery) -> None:
    await safe_edit(call, "📊 <b>Статистика</b>", _menu_kb())
    await call.answer()


# ---------- overview ----------

async def _scoped_overview(actor_tg_id: int) -> dict:
    scope = await visible_shop_ids(actor_tg_id)
    async with pool().acquire() as conn:
        if scope is None:
            users_total = int(await conn.fetchval("SELECT count(*) FROM users") or 0)
            users_active = int(await conn.fetchval(
                "SELECT count(*) FROM users WHERE is_blocked = false") or 0)
            shops_total = int(await conn.fetchval("SELECT count(*) FROM shops") or 0)
            subs_total = int(await conn.fetchval("SELECT count(*) FROM subscriptions") or 0)
            sent_today = int(await conn.fetchval(
                "SELECT count(*) FROM sent_notifications WHERE sent_date = current_date") or 0)
        else:
            if not scope:
                return {"users_total": 0, "users_active": 0, "shops_total": 0,
                        "subs_total": 0, "sent_today": 0}
            users_total = int(await conn.fetchval(
                """
                SELECT count(DISTINCT u.id) FROM users u
                JOIN subscriptions s ON s.user_id = u.id
                WHERE s.shop_id = ANY($1)
                """, scope) or 0)
            users_active = int(await conn.fetchval(
                """
                SELECT count(DISTINCT u.id) FROM users u
                JOIN subscriptions s ON s.user_id = u.id
                WHERE s.shop_id = ANY($1) AND u.is_blocked = false
                """, scope) or 0)
            shops_total = len(scope)
            subs_total = int(await conn.fetchval(
                "SELECT count(*) FROM subscriptions WHERE shop_id = ANY($1)", scope) or 0)
            sent_today = int(await conn.fetchval(
                """
                SELECT count(*) FROM sent_notifications
                WHERE sent_date = current_date AND shop_id = ANY($1)
                """, scope) or 0)
    return {
        "users_total": users_total,
        "users_active": users_active,
        "shops_total": shops_total,
        "subs_total": subs_total,
        "sent_today": sent_today,
    }


@router.callback_query(F.data == "adm:st:overview")
async def cb_overview(call: CallbackQuery) -> None:
    s = await _scoped_overview(call.from_user.id)
    lines = [
        "📊 <b>Обзор</b>",
        "",
        f"👥 Пользователей: {s['users_total']} (активных: {s['users_active']})",
        f"⭐ Подписок: {s['subs_total']}",
        f"🛍 Магазинов: {s['shops_total']}",
        f"📤 Уведомлений сегодня: {s['sent_today']}",
    ]
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="← Назад", callback_data="adm:stats")]])
    await safe_edit(call, "\n".join(lines), kb)
    await call.answer()


# ---------- top shops ----------

@router.callback_query(F.data == "adm:st:top")
async def cb_top(call: CallbackQuery) -> None:
    scope = await visible_shop_ids(call.from_user.id)
    async with pool().acquire() as conn:
        if scope is None:
            rows = await conn.fetch(
                """
                SELECT s.chain_name, s.name, count(*) AS n
                FROM subscriptions sub JOIN shops s ON s.id = sub.shop_id
                GROUP BY s.id ORDER BY n DESC LIMIT 15
                """
            )
        else:
            if not scope:
                rows = []
            else:
                rows = await conn.fetch(
                    """
                    SELECT s.chain_name, s.name, count(*) AS n
                    FROM subscriptions sub JOIN shops s ON s.id = sub.shop_id
                    WHERE s.id = ANY($1)
                    GROUP BY s.id ORDER BY n DESC LIMIT 15
                    """,
                    scope,
                )
    lines = ["🔝 <b>Топ магазинов по подпискам</b>", ""]
    if not rows:
        lines.append("— пусто —")
    for r in rows:
        chain_p = f"[{r['chain_name']}] " if r['chain_name'] else ""
        lines.append(f"  {chain_p}{html.escape(r['name'])} — {r['n']}")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="← Назад", callback_data="adm:stats")]])
    await safe_edit(call, "\n".join(lines), kb)
    await call.answer()


# ---------- notifications ----------

@router.callback_query(F.data == "adm:st:notif")
async def cb_notif(call: CallbackQuery) -> None:
    scope = await visible_shop_ids(call.from_user.id)
    today = date.today()
    async with pool().acquire() as conn:
        scope_filter = ""
        args: list = []
        if scope is not None:
            if not scope:
                await safe_edit(call, "Нет данных.",
                                InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="← Назад", callback_data="adm:stats")]]))
                await call.answer()
                return
            scope_filter = "WHERE shop_id = ANY($1)"
            args.append(scope)

        n_today = int(await conn.fetchval(
            f"SELECT count(*) FROM sent_notifications {scope_filter} {'AND' if scope_filter else 'WHERE'} sent_date = current_date",
            *args,
        ) or 0)
        n_week = int(await conn.fetchval(
            f"SELECT count(*) FROM sent_notifications {scope_filter} {'AND' if scope_filter else 'WHERE'} sent_date >= current_date - 6",
            *args,
        ) or 0)
        n_month = int(await conn.fetchval(
            f"SELECT count(*) FROM sent_notifications {scope_filter} {'AND' if scope_filter else 'WHERE'} sent_date >= current_date - 29",
            *args,
        ) or 0)
        by_event = await conn.fetch(
            f"""
            SELECT event_type, count(*) AS n FROM sent_notifications
            {scope_filter} {'AND' if scope_filter else 'WHERE'} sent_date >= current_date - 6
            GROUP BY event_type ORDER BY n DESC
            """,
            *args,
        )
    lines = ["📩 <b>Уведомления</b>", ""]
    lines.append(f"Сегодня: {n_today}")
    lines.append(f"За 7 дней: {n_week}")
    lines.append(f"За 30 дней: {n_month}")
    if by_event:
        lines.append("")
        lines.append("<b>За 7 дней по типам:</b>")
        for r in by_event:
            lines.append(f"  {html.escape(r['event_type'])}: {r['n']}")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="← Назад", callback_data="adm:stats")]])
    await safe_edit(call, "\n".join(lines), kb)
    await call.answer()


# ---------- CSV export ----------

@router.callback_query(F.data == "adm:st:csvmenu")
async def cb_csv_menu(call: CallbackQuery) -> None:
    rows = [
        [InlineKeyboardButton(text="🛍 Магазины", callback_data="adm:st:csv:shops")],
        [InlineKeyboardButton(text="👥 Пользователи (моего scope)", callback_data="adm:st:csv:users")],
        [InlineKeyboardButton(text="📩 Уведомления (30 дн.)", callback_data="adm:st:csv:notif")],
        [InlineKeyboardButton(text="← Назад", callback_data="adm:stats")],
    ]
    await safe_edit(call, "⬇️ <b>Экспорт CSV</b>", InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


def _csv_bytes(headers: list[str], rows: list[list]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(headers)
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode("utf-8-sig")


@router.callback_query(F.data == "adm:st:csv:shops")
async def cb_csv_shops(call: CallbackQuery) -> None:
    scope = await visible_shop_ids(call.from_user.id)
    async with pool().acquire() as conn:
        if scope is None:
            rs = await conn.fetch(
                """
                SELECT id, name, address, chain_name, cycle_length, anchor_date, is_active,
                       (SELECT count(*) FROM subscriptions WHERE shop_id = shops.id) AS subs
                FROM shops ORDER BY name
                """
            )
        else:
            if not scope:
                rs = []
            else:
                rs = await conn.fetch(
                    """
                    SELECT id, name, address, chain_name, cycle_length, anchor_date, is_active,
                           (SELECT count(*) FROM subscriptions WHERE shop_id = shops.id) AS subs
                    FROM shops WHERE id = ANY($1) ORDER BY name
                    """,
                    scope,
                )
    headers = ["id", "name", "address", "chain_name", "cycle_length", "anchor_date", "is_active", "subs"]
    rows = [[r[h] for h in headers] for r in rs]
    data = _csv_bytes(headers, rows)
    await call.message.answer_document(
        BufferedInputFile(data, filename=f"shops_{date.today().isoformat()}.csv"),
        caption=f"🛍 Магазинов: {len(rows)}",
    )
    await call.answer()


@router.callback_query(F.data == "adm:st:csv:users")
async def cb_csv_users(call: CallbackQuery) -> None:
    scope = await visible_shop_ids(call.from_user.id)
    async with pool().acquire() as conn:
        if scope is None:
            rs = await conn.fetch(
                """
                SELECT u.tg_id, u.username, u.created_at, u.is_blocked, u.pause_until,
                       (SELECT count(*) FROM subscriptions WHERE user_id = u.id) AS subs
                FROM users u ORDER BY u.created_at DESC LIMIT 10000
                """
            )
        else:
            if not scope:
                rs = []
            else:
                rs = await conn.fetch(
                    """
                    SELECT DISTINCT u.tg_id, u.username, u.created_at, u.is_blocked, u.pause_until,
                           (SELECT count(*) FROM subscriptions WHERE user_id = u.id) AS subs
                    FROM users u JOIN subscriptions s ON s.user_id = u.id
                    WHERE s.shop_id = ANY($1)
                    ORDER BY u.created_at DESC LIMIT 10000
                    """,
                    scope,
                )
    headers = ["tg_id", "username", "created_at", "is_blocked", "pause_until", "subs"]
    rows = [[r[h] for h in headers] for r in rs]
    data = _csv_bytes(headers, rows)
    await call.message.answer_document(
        BufferedInputFile(data, filename=f"users_{date.today().isoformat()}.csv"),
        caption=f"👥 Пользователей: {len(rows)}",
    )
    await call.answer()


@router.callback_query(F.data == "adm:st:csv:notif")
async def cb_csv_notif(call: CallbackQuery) -> None:
    scope = await visible_shop_ids(call.from_user.id)
    cutoff = date.today() - timedelta(days=30)
    async with pool().acquire() as conn:
        if scope is None:
            rs = await conn.fetch(
                """
                SELECT user_id, shop_id, event_type, sent_date
                FROM sent_notifications
                WHERE sent_date >= $1
                ORDER BY sent_date DESC LIMIT 50000
                """,
                cutoff,
            )
        else:
            if not scope:
                rs = []
            else:
                rs = await conn.fetch(
                    """
                    SELECT user_id, shop_id, event_type, sent_date
                    FROM sent_notifications
                    WHERE sent_date >= $1 AND shop_id = ANY($2)
                    ORDER BY sent_date DESC LIMIT 50000
                    """,
                    cutoff, scope,
                )
    headers = ["user_id", "shop_id", "event_type", "sent_date"]
    rows = [[r[h] for h in headers] for r in rs]
    data = _csv_bytes(headers, rows)
    await call.message.answer_document(
        BufferedInputFile(data, filename=f"notifications_{date.today().isoformat()}.csv"),
        caption=f"📩 Записей: {len(rows)}",
    )
    await call.answer()
