import html
import logging
from dataclasses import dataclass
from datetime import date, datetime, time

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

from data.repos.notifier_repo import (
    already_sent,
    fetch_notify_candidates,
    mark_blocked,
    mark_sent,
)
from services.chat_journal import journal
from services.cycle import EventType, days_until, humanize_days, resolve_cycle_info

log = logging.getLogger(__name__)

NOTIFY_AT = time(9, 0)
ARRIVAL_LEAD_DAYS = 1
CHEAP_DAY_LEAD_DAYS = 0

EVENT_HEADERS: dict[str, str] = {
    "arrival": "🚚 Завоз",
    "cheap_day": "💰 Дешёвый день",
}
EVENT_ORDER: list[str] = ["arrival", "cheap_day"]


@dataclass(frozen=True)
class Trigger:
    shop_id: int
    shop_name: str
    address: str
    event_type: str
    lead_days: int = 0  # 0 = "сегодня"; 1 = "завтра", etc.


def format_message(triggers: list[Trigger]) -> str:
    if not triggers:
        return ""
    by_type: dict[str, list[Trigger]] = {}
    for t in triggers:
        by_type.setdefault(t.event_type, []).append(t)

    lines: list[str] = ["🔔 <b>Уведомление по твоим магазинам:</b>", ""]
    for et in EVENT_ORDER:
        items = by_type.get(et)
        if not items:
            continue
        lines.append(f"<b>{EVENT_HEADERS[et]}</b>")
        for tr in items:
            when = humanize_days(tr.lead_days)
            lines.append(
                f"   • <b>{html.escape(tr.shop_name)}</b> — {html.escape(tr.address)} ({when})"
            )
        lines.append("")
    return "\n".join(lines).rstrip()


async def run_for_minute(bot: Bot, when: datetime) -> None:
    """Both events fire at a fixed time (9:00) — nothing to do off-schedule."""
    if when.time().replace(second=0, microsecond=0) != NOTIFY_AT:
        return

    today = when.date()
    rows = await fetch_notify_candidates(today)
    if not rows:
        return

    by_user: dict[int, list[Trigger]] = {}
    tg_by_user: dict[int, int] = {}
    for row in rows:
        info = resolve_cycle_info(
            row["cycle_length"], row["anchor_date"], row["monthly_weekday"], today,
            monthly_occurrence=int(row["monthly_occurrence"] or 1),
        )
        if info is None:
            continue
        user_id = int(row["user_id"])
        tg_by_user[user_id] = int(row["tg_id"])

        if row["notify_arrival"] and days_until(today, info, EventType.ARRIVAL) == ARRIVAL_LEAD_DAYS:
            by_user.setdefault(user_id, []).append(Trigger(
                shop_id=int(row["shop_id"]), shop_name=row["name"], address=row["address"],
                event_type="arrival", lead_days=ARRIVAL_LEAD_DAYS,
            ))
        if row["notify_cheap_day"] and days_until(today, info, EventType.MAX_DISCOUNT) == CHEAP_DAY_LEAD_DAYS:
            by_user.setdefault(user_id, []).append(Trigger(
                shop_id=int(row["shop_id"]), shop_name=row["name"], address=row["address"],
                event_type="cheap_day", lead_days=CHEAP_DAY_LEAD_DAYS,
            ))

    for user_id, triggers in by_user.items():
        sent = await already_sent(
            user_id, [(t.shop_id, t.event_type) for t in triggers], today,
        )
        fresh = [t for t in triggers if (t.shop_id, t.event_type) not in sent]
        if not fresh:
            continue
        await _send_and_mark(bot, user_id, fresh, today, tg_id=tg_by_user.get(user_id))


async def _send_and_mark(
    bot: Bot, user_id: int, triggers: list[Trigger], today: date, *, tg_id: int | None = None,
) -> None:
    text = format_message(triggers)
    if tg_id is None:
        tg_id = await _get_tg_id(user_id)
    if tg_id is None:
        return
    try:
        msg = await bot.send_message(tg_id, text)
    except TelegramForbiddenError:
        log.warning("user %s blocked the bot", tg_id)
        await mark_blocked(user_id)
        return
    except TelegramRetryAfter as e:
        log.warning("flood limit, retry after %s sec", e.retry_after)
        return
    except Exception:
        log.exception("send failed for user_id=%s", user_id)
        return

    journal.record(tg_id, msg.message_id)
    await mark_sent(user_id, [(t.shop_id, t.event_type) for t in triggers], today)


async def _get_tg_id(user_id: int) -> int | None:
    from data.db import pool
    async with pool().acquire() as conn:
        return await conn.fetchval("SELECT tg_id FROM users WHERE id = $1", user_id)
