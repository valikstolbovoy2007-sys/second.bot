"""Superadmin health-check (TZ §10.8)."""
import asyncio
import html
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from config import settings
from data.db import pool
from data.repos.notifier_repo import get_last_run
from handlers.admin.filters import IsSuperAdmin
from handlers.admin.ui import safe_edit
from services.audit import write as audit_write

log = logging.getLogger(__name__)
router = Router(name="sa_health")
router.message.filter(IsSuperAdmin())
router.callback_query.filter(IsSuperAdmin())

TZ = ZoneInfo("Europe/Moscow")


async def _check_db() -> tuple[bool, str]:
    try:
        t0 = time.perf_counter()
        async with pool().acquire() as conn:
            await conn.fetchval("SELECT 1")
        dt_ms = (time.perf_counter() - t0) * 1000
        return True, f"OK ({dt_ms:.0f} мс)"
    except Exception as e:
        return False, f"FAIL: {e}"


async def _check_telegram(bot: Bot) -> tuple[bool, str]:
    try:
        t0 = time.perf_counter()
        me = await asyncio.wait_for(bot.get_me(), timeout=10)
        dt_ms = (time.perf_counter() - t0) * 1000
        return True, f"OK @{me.username} ({dt_ms:.0f} мс)"
    except Exception as e:
        return False, f"FAIL: {e}"


async def _check_scheduler() -> tuple[bool, str]:
    try:
        last = await get_last_run()
        if last is None:
            return False, "ни одного тика"
        if last.tzinfo is None:
            last = last.replace(tzinfo=TZ)
        now = datetime.now(TZ)
        gap = (now - last).total_seconds()
        ok = gap < 180  # 3 minutes
        return ok, f"последний тик {last.strftime('%d.%m %H:%M')} ({int(gap)} сек назад)"
    except Exception as e:
        return False, f"FAIL: {e}"


async def _check_proxy() -> tuple[bool, str]:
    if not settings.PROXY_URL:
        return True, "не настроен"
    return True, f"настроен: {settings.PROXY_URL[:40]}…"


async def _run_all(bot: Bot) -> str:
    db_ok, db_msg = await _check_db()
    tg_ok, tg_msg = await _check_telegram(bot)
    sc_ok, sc_msg = await _check_scheduler()
    pr_ok, pr_msg = await _check_proxy()

    def mark(ok: bool) -> str:
        return "🟢" if ok else "🔴"

    overall = all([db_ok, tg_ok, sc_ok, pr_ok])
    head = "✅ <b>Здоровье: всё ок</b>" if overall else "⚠️ <b>Есть проблемы</b>"
    return (
        f"{head}\n\n"
        f"{mark(db_ok)} <b>База данных:</b> {html.escape(db_msg)}\n"
        f"{mark(tg_ok)} <b>Telegram API:</b> {html.escape(tg_msg)}\n"
        f"{mark(sc_ok)} <b>Шедулер:</b> {html.escape(sc_msg)}\n"
        f"{mark(pr_ok)} <b>Прокси:</b> {html.escape(pr_msg)}\n"
    )


def _kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Прогнать снова", callback_data="sa:health:run")],
        [InlineKeyboardButton(text="← Назад", callback_data="sa:menu")],
    ])


@router.callback_query(F.data == "sa:health:run")
async def cb_run(call: CallbackQuery, bot: Bot) -> None:
    await call.answer("Проверяю…")
    text = await _run_all(bot)
    await audit_write(call.from_user.id, "health.check", None, None, None)
    await safe_edit(call, text, _kb())
