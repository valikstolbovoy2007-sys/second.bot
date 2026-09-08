from datetime import date, datetime, time
from typing import Iterable

from data.db import pool


async def users_due_at(t: time, today: date) -> list[int]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id FROM users
            WHERE notify_time = $1
              AND is_blocked = false
              AND (pause_until IS NULL OR pause_until <= $2)
            """,
            t.replace(second=0, microsecond=0), today,
        )
    return [int(r["id"]) for r in rows]


async def fetch_user_subscriptions(user_id: int) -> list[dict]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT s.id          AS shop_id,
                   s.name        AS name,
                   s.address     AS address,
                   s.cycle_length,
                   s.anchor_date,
                   s.monthly_weekday,
                   s.monthly_occurrence,
                   sub.notify_arrival,
                   sub.notify_max_discount,
                   sub.notify_middle
            FROM subscriptions sub
            JOIN shops s ON s.id = sub.shop_id
            WHERE sub.user_id = $1 AND s.is_active = true
            """,
            user_id,
        )
    return [dict(r) for r in rows]


async def fetch_user_weekdays(user_id: int) -> dict[int, set[int]]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT shop_id, weekday FROM notification_weekdays
            WHERE user_id = $1
            """,
            user_id,
        )
    out: dict[int, set[int]] = {}
    for r in rows:
        out.setdefault(int(r["shop_id"]), set()).add(int(r["weekday"]))
    return out


async def fetch_lead_event_candidates(today: date) -> list[dict]:
    """One row per (user, shop) subscription with arrival/max_discount
    notifications enabled, joined with the shop's cycle fields and the
    user's tg_id + global notify_time (fallback when no per-event time is
    set). Used by the lead-days-aware notification engine — see
    services.notifier.run_lead_events.
    """
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT u.id AS user_id, u.tg_id, u.notify_time AS global_time,
                   s.id AS shop_id, s.name, s.address,
                   s.cycle_length, s.anchor_date,
                   s.monthly_weekday, s.monthly_occurrence,
                   sub.notify_arrival, sub.arrival_lead_days, sub.arrival_notify_time,
                   sub.notify_max_discount, sub.discount_lead_days, sub.discount_notify_time
            FROM subscriptions sub
            JOIN shops s ON s.id = sub.shop_id
            JOIN users u ON u.id = sub.user_id
            WHERE s.is_active = true
              AND u.is_blocked = false
              AND (u.pause_until IS NULL OR u.pause_until <= $1)
              AND (sub.notify_arrival OR sub.notify_max_discount)
            """,
            today,
        )
    return [dict(r) for r in rows]


async def already_sent(
    user_id: int,
    items: Iterable[tuple[int, str]],
    today: date,
) -> set[tuple[int, str]]:
    items = list(items)
    if not items:
        return set()
    shop_ids = [i[0] for i in items]
    events = [i[1] for i in items]
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT shop_id, event_type FROM sent_notifications
            WHERE user_id = $1
              AND sent_date = $2
              AND (shop_id, event_type) IN (
                  SELECT * FROM unnest($3::int[], $4::text[])
              )
            """,
            user_id, today, shop_ids, events,
        )
    return {(int(r["shop_id"]), r["event_type"]) for r in rows}


async def mark_sent(
    user_id: int,
    items: Iterable[tuple[int, str]],
    today: date,
) -> None:
    items = list(items)
    if not items:
        return
    async with pool().acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO sent_notifications (user_id, shop_id, event_type, sent_date)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT DO NOTHING
            """,
            [(user_id, shop_id, event, today) for shop_id, event in items],
        )


async def mark_blocked(user_id: int) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            "UPDATE users SET is_blocked = true WHERE id = $1", user_id,
        )


async def get_last_run() -> datetime | None:
    async with pool().acquire() as conn:
        return await conn.fetchval("SELECT last_run_at FROM scheduler_state WHERE id = 1")


async def set_last_run(t: datetime) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            "UPDATE scheduler_state SET last_run_at = $1 WHERE id = 1", t,
        )
