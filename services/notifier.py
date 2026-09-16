import asyncio
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
from services.cycle import EventType, days_until, next_event_date, resolve_cycle_info

log = logging.getLogger(__name__)

NOTIFY_AT = time(9, 0)
# День дешёвой цены — это день перед завозом. Чтобы уведомления не падали в
# один день: о дешёвом дне пишем за день до него (за 2 дня до завоза),
# о завозе — в день завоза, о середине цикла — в сам день середины.
ARRIVAL_LEAD_DAYS = 0
CHEAP_DAY_LEAD_DAYS = 1
MIDDLE_LEAD_DAYS = 0

_RU_WEEKDAYS = [
    "Понедельник", "Вторник", "Среда", "Четверг",
    "Пятница", "Суббота", "Воскресенье",
]

# Кэш username бота для deep-ссылок. Устанавливается один раз при старте.
_bot_username: str | None = None


def set_bot_username(username: str) -> None:
    global _bot_username
    _bot_username = username


def get_bot_username() -> str | None:
    return _bot_username


@dataclass(frozen=True)
class Trigger:
    shop_id: int
    shop_name: str
    address: str
    event_type: str
    lead_days: int = 0  # 0 = "сегодня"; 1 = "завтра", etc.
    arrival_weekday: str | None = None


def _shop_deep_link(shop_id: int) -> str | None:
    username = _bot_username
    if not username:
        return None
    return f"https://t.me/{username}?start=shop_{shop_id}"


def format_shop_message(trigger: Trigger) -> str:
    """Одно уведомление: один магазин, одно событие + deep link."""
    link = _shop_deep_link(trigger.shop_id)
    if trigger.event_type == "cheap_day":
        if trigger.arrival_weekday:
            line = f"💰 Завтра самый дешёвый день (завоз в {trigger.arrival_weekday})"
        else:
            line = "💰 Завтра самый дешёвый день"
    elif trigger.event_type == "middle":
        line = "⚖️ Сегодня середина цикла — скидка средняя, цена не дорогая и не дешёвая"
    else:
        line = "🚚 Завтра день завоза — самая дорогая цена"
    lines = [
        f"🔔 <b>{html.escape(trigger.shop_name)}</b>",
        line,
        f"📍 {html.escape(trigger.address)}",
    ]
    if link:
        lines.append("")
        lines.append(f'<a href="{link}">🔗 Открыть карточку</a>')
    return "\n".join(lines)


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
        # Уведомление о дешёвом дне: «завтра самый дешёвый день». Завоз приходит
        # на следующий день после дешёвого — weekday нужен для текста уведомления.
        if row["notify_cheap_day"] and days_until(today, info, EventType.MAX_DISCOUNT) == CHEAP_DAY_LEAD_DAYS:
            arrival_date = next_event_date(today, info, EventType.ARRIVAL)
            by_user.setdefault(user_id, []).append(Trigger(
                shop_id=int(row["shop_id"]), shop_name=row["name"], address=row["address"],
                event_type="cheap_day", lead_days=CHEAP_DAY_LEAD_DAYS,
                arrival_weekday=_RU_WEEKDAYS[arrival_date.weekday()],
            ))
        if row["notify_middle"] and days_until(today, info, EventType.MIDDLE) == MIDDLE_LEAD_DAYS:
            by_user.setdefault(user_id, []).append(Trigger(
                shop_id=int(row["shop_id"]), shop_name=row["name"], address=row["address"],
                event_type="middle", lead_days=MIDDLE_LEAD_DAYS,
            ))

    for user_id, triggers in by_user.items():
        sent = await already_sent(
            user_id, [(t.shop_id, t.event_type) for t in triggers], today,
        )
        fresh = [t for t in triggers if (t.shop_id, t.event_type) not in sent]
        if not fresh:
            continue
        await _send_per_shop(bot, user_id, fresh, today, tg_id=tg_by_user.get(user_id))


async def _send_per_shop(
    bot: Bot,
    user_id: int,
    triggers: list[Trigger],
    today: date,
    *,
    tg_id: int | None = None,
) -> None:
    """Отправить по одному сообщению на каждый триггер (магазин + событие)."""
    if tg_id is None:
        tg_id = await _get_tg_id(user_id)
    if tg_id is None:
        return

    sent_pairs: list[tuple[int, str]] = []
    for i, trigger in enumerate(triggers):
        text = format_shop_message(trigger)
        try:
            msg = await bot.send_message(tg_id, text, disable_web_page_preview=True)
        except TelegramForbiddenError:
            log.warning("user %s blocked the bot", tg_id)
            await mark_blocked(user_id)
            return
        except TelegramRetryAfter as e:
            log.warning("flood limit, retry after %s sec", e.retry_after)
            return
        except Exception:
            log.exception("send failed for user_id=%s shop_id=%s", user_id, trigger.shop_id)
            continue

        journal.record(tg_id, msg.message_id)
        sent_pairs.append((trigger.shop_id, trigger.event_type))

        # Пауза между сообщениями: Telegram → 20 msg/min на тот же чат.
        if i < len(triggers) - 1:
            await asyncio.sleep(0.1)

    if sent_pairs:
        await mark_sent(user_id, sent_pairs, today)


async def _get_tg_id(user_id: int) -> int | None:
    from data.db import pool
    async with pool().acquire() as conn:
        return await conn.fetchval("SELECT tg_id FROM users WHERE id = $1", user_id)
