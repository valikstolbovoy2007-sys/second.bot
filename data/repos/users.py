from dataclasses import dataclass
from datetime import date, time

from data.db import pool


@dataclass(frozen=True)
class UserSettings:
    id: int
    tg_id: int
    notify_time: time
    pause_until: date | None


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


async def is_admin(tg_id: int) -> bool:
    from data.repos.admin_roles import is_admin as _is_admin
    return await _is_admin(tg_id)


async def get_settings(user_id: int) -> UserSettings | None:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, tg_id, notify_time, pause_until FROM users WHERE id = $1",
            user_id,
        )
    if not row:
        return None
    return UserSettings(
        id=row["id"],
        tg_id=row["tg_id"],
        notify_time=row["notify_time"],
        pause_until=row["pause_until"],
    )


async def set_notify_time(user_id: int, t: time) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            "UPDATE users SET notify_time = $2 WHERE id = $1", user_id, t,
        )


async def set_pause_until(user_id: int, d: date | None) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            "UPDATE users SET pause_until = $2 WHERE id = $1", user_id, d,
        )
