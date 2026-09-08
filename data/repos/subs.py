from dataclasses import dataclass
from datetime import time

from data.db import pool
from data.repos.shops import Shop, _row_to_shop


@dataclass(frozen=True)
class SubFlags:
    notify_arrival: bool
    notify_max_discount: bool
    notify_middle: bool


VALID_FLAGS = {"notify_arrival", "notify_max_discount", "notify_middle"}

# "arrival" / "max_discount" — the two events configurable through the
# multi-step notification wizard (see handlers/settings.py). Each maps to
# its own enabled-flag and lead_days/notify_time column pair.
_EVENT_COLUMNS = {
    "arrival": ("notify_arrival", "arrival_lead_days", "arrival_notify_time"),
    "max_discount": ("notify_max_discount", "discount_lead_days", "discount_notify_time"),
}


@dataclass(frozen=True)
class EventNotifySetting:
    enabled: bool
    lead_days: int
    notify_time: time | None  # None = use the user's global notify_time


async def get_event_settings(user_id: int, shop_id: int) -> dict[str, EventNotifySetting] | None:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT notify_arrival, arrival_lead_days, arrival_notify_time,
                   notify_max_discount, discount_lead_days, discount_notify_time
            FROM subscriptions WHERE user_id = $1 AND shop_id = $2
            """,
            user_id, shop_id,
        )
    if not row:
        return None
    return {
        "arrival": EventNotifySetting(
            enabled=row["notify_arrival"],
            lead_days=row["arrival_lead_days"],
            notify_time=row["arrival_notify_time"],
        ),
        "max_discount": EventNotifySetting(
            enabled=row["notify_max_discount"],
            lead_days=row["discount_lead_days"],
            notify_time=row["discount_notify_time"],
        ),
    }


async def set_event_notify(
    user_id: int, shop_id: int, event: str, lead_days: int, notify_time: time | None,
) -> None:
    if event not in _EVENT_COLUMNS:
        raise ValueError(f"unknown event: {event}")
    flag_col, lead_col, time_col = _EVENT_COLUMNS[event]
    async with pool().acquire() as conn:
        await conn.execute(
            f"""
            UPDATE subscriptions
            SET {flag_col} = true, {lead_col} = $3, {time_col} = $4
            WHERE user_id = $1 AND shop_id = $2
            """,
            user_id, shop_id, lead_days, notify_time,
        )


async def disable_event_notify(user_id: int, shop_id: int, event: str) -> None:
    if event not in _EVENT_COLUMNS:
        raise ValueError(f"unknown event: {event}")
    flag_col, _lead_col, _time_col = _EVENT_COLUMNS[event]
    async with pool().acquire() as conn:
        await conn.execute(
            f"UPDATE subscriptions SET {flag_col} = false WHERE user_id = $1 AND shop_id = $2",
            user_id, shop_id,
        )


async def is_subscribed(user_id: int, shop_id: int) -> bool:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM subscriptions WHERE user_id = $1 AND shop_id = $2",
            user_id, shop_id,
        )
    return row is not None


async def subscribe(user_id: int, shop_id: int) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO subscriptions (user_id, shop_id)
            VALUES ($1, $2)
            ON CONFLICT (user_id, shop_id) DO NOTHING
            """,
            user_id, shop_id,
        )


async def unsubscribe(user_id: int, shop_id: int) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            "DELETE FROM subscriptions WHERE user_id = $1 AND shop_id = $2",
            user_id, shop_id,
        )


async def count_subscriptions(user_id: int) -> int:
    async with pool().acquire() as conn:
        return int(await conn.fetchval(
            "SELECT count(*) FROM subscriptions WHERE user_id = $1", user_id
        ) or 0)


async def subscribed_shop_ids(user_id: int) -> set[int]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT shop_id FROM subscriptions WHERE user_id = $1", user_id,
        )
    return {int(r["shop_id"]) for r in rows}


async def list_subscribed(user_id: int, limit: int, offset: int) -> tuple[list[Shop], int]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT s.*, count(*) OVER () AS _total FROM shops s
            JOIN subscriptions sub ON sub.shop_id = s.id
            WHERE sub.user_id = $1 AND s.is_active = true
            ORDER BY s.chain_name NULLS LAST, s.name
            LIMIT $2 OFFSET $3
            """,
            user_id, limit, offset,
        )
    total = int(rows[0]["_total"]) if rows else 0
    return [_row_to_shop(r) for r in rows], total


async def subscribe_all(user_id: int) -> int:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            WITH inserted AS (
                INSERT INTO subscriptions (user_id, shop_id)
                SELECT $1, id FROM shops WHERE is_active = true
                ON CONFLICT (user_id, shop_id) DO NOTHING
                RETURNING shop_id
            )
            SELECT count(*) AS n FROM inserted
            """,
            user_id,
        )
    return int(row["n"] or 0)


async def unsubscribe_all(user_id: int) -> int:
    async with pool().acquire() as conn:
        result = await conn.execute(
            "DELETE FROM subscriptions WHERE user_id = $1", user_id
        )
    return int(result.split()[-1]) if result.startswith("DELETE") else 0


async def get_flags(user_id: int, shop_id: int) -> SubFlags | None:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT notify_arrival, notify_max_discount, notify_middle
            FROM subscriptions WHERE user_id = $1 AND shop_id = $2
            """,
            user_id, shop_id,
        )
    if not row:
        return None
    return SubFlags(
        notify_arrival=row["notify_arrival"],
        notify_max_discount=row["notify_max_discount"],
        notify_middle=row["notify_middle"],
    )


async def toggle_flag(user_id: int, shop_id: int, field: str) -> bool:
    if field not in VALID_FLAGS:
        raise ValueError(f"unknown flag: {field}")
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE subscriptions SET {field} = NOT {field}
            WHERE user_id = $1 AND shop_id = $2
            RETURNING {field}
            """,
            user_id, shop_id,
        )
    return bool(row[field]) if row else False


async def get_weekdays(user_id: int, shop_id: int) -> set[int]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT weekday FROM notification_weekdays WHERE user_id = $1 AND shop_id = $2",
            user_id, shop_id,
        )
    return {int(r["weekday"]) for r in rows}


async def toggle_weekday(user_id: int, shop_id: int, weekday: int) -> bool:
    if not 0 <= weekday <= 6:
        raise ValueError("weekday must be 0..6")
    async with pool().acquire() as conn:
        async with conn.transaction():
            existing = await conn.fetchval(
                """
                SELECT 1 FROM notification_weekdays
                WHERE user_id = $1 AND shop_id = $2 AND weekday = $3
                """,
                user_id, shop_id, weekday,
            )
            if existing:
                await conn.execute(
                    """
                    DELETE FROM notification_weekdays
                    WHERE user_id = $1 AND shop_id = $2 AND weekday = $3
                    """,
                    user_id, shop_id, weekday,
                )
                return False
            await conn.execute(
                """
                INSERT INTO notification_weekdays (user_id, shop_id, weekday)
                VALUES ($1, $2, $3)
                """,
                user_id, shop_id, weekday,
            )
            return True
