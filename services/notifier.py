import html
import logging
from dataclasses import dataclass
from datetime import date, datetime

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

from data.repos.notifier_repo import (
    already_sent,
    fetch_user_subscriptions,
    fetch_user_weekdays,
    mark_blocked,
    mark_sent,
    users_due_at,
)
from services.cycle import CycleInfo, EventType, events_on

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


def pick_events_for_subscription(sub: dict, today: date, weekdays: set[int]) -> list[str]:
    events: list[str] = []
    if sub["cycle_length"] and sub["anchor_date"]:
        info = CycleInfo(sub["cycle_length"], sub["anchor_date"])
        for ev in events_on(today, info):
            if sub.get(_EVENT_FIELD[ev]):
                events.append(_EVENT_NAME[ev])
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

    lines: list[str] = ["🔔 <b>Сегодня в твоих магазинах:</b>", ""]
    for et in EVENT_ORDER:
        items = by_type.get(et)
        if not items:
            continue
        lines.append(f"<b>{EVENT_HEADERS[et]}</b>")
        for tr in items:
            lines.append(
                f"   • <b>{html.escape(tr.shop_name)}</b> — {html.escape(tr.address)}"
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
    if not user_ids:
        return

    log.info("notify tick %s: %d users due", minute_time.strftime("%H:%M"), len(user_ids))

    for user_id in user_ids:
        triggers = await collect_triggers(user_id, today)
        if not triggers:
            continue
        text = format_message(triggers)
        tg_id = await _get_tg_id(user_id)
        if tg_id is None:
            continue
        try:
            await bot.send_message(tg_id, text)
        except TelegramForbiddenError:
            log.warning("user %s blocked the bot", tg_id)
            await mark_blocked(user_id)
            continue
        except TelegramRetryAfter as e:
            log.warning("flood limit, retry after %s sec", e.retry_after)
            continue
        except Exception:
            log.exception("send failed for user_id=%s", user_id)
            continue

        await mark_sent(
            user_id,
            [(t.shop_id, t.event_type) for t in triggers],
            today,
        )


async def _get_tg_id(user_id: int) -> int | None:
    from data.db import pool
    async with pool().acquire() as conn:
        return await conn.fetchval("SELECT tg_id FROM users WHERE id = $1", user_id)
