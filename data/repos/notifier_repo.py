from datetime import date, datetime
from typing import Iterable

from data.db import pool


async def fetch_notify_candidates(today: date) -> list[dict]:
    """One row per (user, shop) subscription for a user who has at least
    one of the two global notification switches on, joined with the shop's
    cycle fields and the user's tg_id. Arrival fires 1 day before, at
    9:00; cheap-day fires the same day, at 9:00 — both fixed (see
    services.notifier.run_for_minute).
    """
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT u.id AS user_id, u.tg_id,
                   s.id AS shop_id, s.name, s.address,
                   s.cycle_length, s.anchor_date,
                   s.monthly_weekday, s.monthly_occurrence,
                   u.notify_arrival, u.notify_cheap_day
            FROM subscriptions sub
            JOIN shops s ON s.id = sub.shop_id
            JOIN users u ON u.id = sub.user_id
            WHERE s.is_active = true
              AND u.is_blocked = false
              AND (u.pause_until IS NULL OR u.pause_until <= $1)
              AND (u.notify_arrival OR u.notify_cheap_day)
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
