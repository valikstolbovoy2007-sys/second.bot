from dataclasses import dataclass
from datetime import date

from data.db import pool


@dataclass(frozen=True)
class UserSettings:
    id: int
    tg_id: int
    pause_until: date | None
    notify_arrival: bool
    notify_cheap_day: bool


async def upsert_user(tg_id: int, username: str | None) -> int:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO users (tg_id, username)
            VALUES ($1, $2)
            ON CONFLICT (tg_id) DO UPDATE SET username = EXCLUDED.username
            RETURNING id
            """,
            tg_id,
            username,
        )
    return row["id"]


async def get_tg_id_by_username(username: str) -> int | None:
    """Only finds users who have interacted with the bot at least once —
    Telegram doesn't expose a username -> id lookup, so this relies on the
    `users.username` snapshot taken on their last /start or message."""
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT tg_id FROM users WHERE username ILIKE $1", username,
        )
    return int(row["tg_id"]) if row else None


async def is_admin(tg_id: int) -> bool:
    from data.repos.admin_roles import is_admin as _is_admin
    return await _is_admin(tg_id)


async def get_settings(user_id: int) -> UserSettings | None:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, tg_id, pause_until, notify_arrival, notify_cheap_day "
            "FROM users WHERE id = $1",
            user_id,
        )
    if not row:
        return None
    return UserSettings(
        id=row["id"],
        tg_id=row["tg_id"],
        pause_until=row["pause_until"],
        notify_arrival=row["notify_arrival"],
        notify_cheap_day=row["notify_cheap_day"],
    )


async def set_pause_until(user_id: int, d: date | None) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            "UPDATE users SET pause_until = $2 WHERE id = $1", user_id, d,
        )


async def toggle_notify_arrival(user_id: int) -> bool:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE users SET notify_arrival = NOT notify_arrival WHERE id = $1 "
            "RETURNING notify_arrival",
            user_id,
        )
    return bool(row["notify_arrival"])


async def toggle_notify_cheap_day(user_id: int) -> bool:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE users SET notify_cheap_day = NOT notify_cheap_day WHERE id = $1 "
            "RETURNING notify_cheap_day",
            user_id,
        )
    return bool(row["notify_cheap_day"])
