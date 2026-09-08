import html
import logging
from dataclasses import dataclass
from datetime import date, datetime

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

from data.repos.notifier_repo import (
    already_sent,
    fetch_lead_event_candidates,
    fetch_user_subscriptions,
    fetch_user_weekdays,
    mark_blocked,
    mark_sent,
    users_due_at,
)
from services.cycle import EventType, days_until, events_on, humanize_days, resolve_cycle_info

log = logging.getLogger(__name__)


EVENT_HEADERS: dict[str, str] = {
    "arrival": "🆕 День завоза",
    "max_discount": "💰 Максимальная скидка (завтра новый завоз)",
    "middle": "⚖️ Середина цикла",
    "weekday": "📅 Напоминание",
}
EVENT_ORDER: list[str] = ["arrival", "max_discount", "middle", "weekday"]

_EVENT_FIELD: dict[EventType, str] = {
    EventType.ARRIVAL: "notify_arrival",
    EventType.MAX_DISCOUNT: "notify_max_discount",
    EventType.MIDDLE: "notify_middle",
}
_EVENT_NAME: dict[EventType, str] = {
    EventType.ARRIVAL: "arrival",
    EventType.MAX_DISCOUNT: "max_discount",
    EventType.MIDDLE: "middle",
}


@dataclass(frozen=True)
class Trigger:
    shop_id: int
    shop_name: str
    address: str
    event_type: str
    lead_days: int = 0  # 0 = "today"; >0 = "N days ahead" (arrival/max_discount only)


def pick_events_for_subscription(sub: dict, today: date, weekdays: set[int]) -> list[str]:
    """Same-day events only — arrival/max_discount now go through the
    lead-days-aware engine (see run_lead_events) so they're skipped here.
    """
    events: list[str] = []
    info = resolve_cycle_info(
        sub["cycle_length"], sub["anchor_date"], sub.get("monthly_weekday"), today,
        monthly_occurrence=int(sub.get("monthly_occurrence") or 1),
    )
    if info is not None:
        if EventType.MIDDLE in events_on(today, info) and sub.get("notify_middle"):
            events.append("middle")
    else:
        if today.weekday() in weekdays:
            events.append("weekday")
    return events


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


async def collect_triggers(user_id: int, today: date) -> list[Trigger]:
    subs = await fetch_user_subscriptions(user_id)
    if not subs:
        return []
    weekdays_map = await fetch_user_weekdays(user_id)

    candidates: list[Trigger] = []
    for sub in subs:
        events = pick_events_for_subscription(
            sub, today, weekdays_map.get(int(sub["shop_id"]), set())
        )
        for ev in events:
            candidates.append(Trigger(
                shop_id=int(sub["shop_id"]),
                shop_name=sub["name"],
                address=sub["address"],
                event_type=ev,
            ))

    if not candidates:
        return []

    sent = await already_sent(
        user_id, [(c.shop_id, c.event_type) for c in candidates], today
    )
    return [c for c in candidates if (c.shop_id, c.event_type) not in sent]


async def run_for_minute(bot: Bot, when: datetime) -> None:
    today = when.date()
    minute_time = when.time().replace(second=0, microsecond=0)
    user_ids = await users_due_at(minute_time, today)
    if user_ids:
        log.info("notify tick %s: %d users due", minute_time.strftime("%H:%M"), len(user_ids))

    for user_id in user_ids:
        triggers = await collect_triggers(user_id, today)
        if not triggers:
            continue
        await _send_and_mark(bot, user_id, triggers, today)

    await run_lead_events(bot, when)


async def run_lead_events(bot: Bot, when: datetime) -> None:
    """Fires arrival/max_discount notifications configured with a custom
    lead time and/or a per-event notify time (the per-shop wizard) —
    independent of the user's global notify_time, checked every tick.
    """
    today = when.date()
    tick = when.time().replace(second=0, microsecond=0)
    rows = await fetch_lead_event_candidates(today)
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
        global_time = row["global_time"]
        tg_by_user[int(row["user_id"])] = int(row["tg_id"])

        if row["notify_arrival"]:
            eff = (row["arrival_notify_time"] or global_time).replace(second=0, microsecond=0)
            lead = int(row["arrival_lead_days"] or 0)
            if eff == tick and days_until(today, info, EventType.ARRIVAL) == lead:
                by_user.setdefault(int(row["user_id"]), []).append(Trigger(
                    shop_id=int(row["shop_id"]), shop_name=row["name"],
                    address=row["address"], event_type="arrival", lead_days=lead,
                ))
        if row["notify_max_discount"]:
            eff = (row["discount_notify_time"] or global_time).replace(second=0, microsecond=0)
            lead = int(row["discount_lead_days"] or 0)
            if eff == tick and days_until(today, info, EventType.MAX_DISCOUNT) == lead:
                by_user.setdefault(int(row["user_id"]), []).append(Trigger(
                    shop_id=int(row["shop_id"]), shop_name=row["name"],
                    address=row["address"], event_type="max_discount", lead_days=lead,
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
        await bot.send_message(tg_id, text)
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

    await mark_sent(user_id, [(t.shop_id, t.event_type) for t in triggers], today)


async def _get_tg_id(user_id: int) -> int | None:
    from data.db import pool
    async with pool().acquire() as conn:
        return await conn.fetchval("SELECT tg_id FROM users WHERE id = $1", user_id)
