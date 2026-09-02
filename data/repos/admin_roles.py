import time
from dataclasses import dataclass
from datetime import datetime

from data.db import pool


@dataclass(frozen=True)
class AdminRow:
    tg_id: int
    role: str
    added_by: int | None
    added_at: datetime
    note: str | None
    username: str | None


# Admin filters fire on every callback; the admin set is small and rarely
# changes, so a short TTL avoids hammering the DB on the hot path.
_ROLE_CACHE: dict[int, tuple[float, str | None]] = {}
_ROLE_TTL = 30.0


def _invalidate_role(tg_id: int | None = None) -> None:
    if tg_id is None:
        _ROLE_CACHE.clear()
    else:
        _ROLE_CACHE.pop(tg_id, None)


async def get_role(tg_id: int) -> str | None:
    now = time.time()
    cached = _ROLE_CACHE.get(tg_id)
    if cached and now - cached[0] < _ROLE_TTL:
        return cached[1]
    async with pool().acquire() as conn:
        row = await conn.fetchrow("SELECT role FROM admins WHERE tg_id = $1", tg_id)
    role = row["role"] if row else None
    _ROLE_CACHE[tg_id] = (now, role)
    return role


async def is_admin(tg_id: int) -> bool:
    return await get_role(tg_id) is not None


async def is_super_admin(tg_id: int) -> bool:
    return await get_role(tg_id) == "super_admin"


async def list_admins() -> list[AdminRow]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT a.tg_id, a.role, a.added_by, a.added_at, a.note, u.username
            FROM admins a
            LEFT JOIN users u ON u.tg_id = a.tg_id
            ORDER BY a.role DESC, a.added_at
            """
        )
    return [AdminRow(**dict(r)) for r in rows]


async def add_admin(tg_id: int, role: str, added_by: int, note: str | None = None) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO admins (tg_id, role, added_by, note) VALUES ($1, $2, $3, $4)
            ON CONFLICT (tg_id) DO UPDATE SET role = EXCLUDED.role, note = EXCLUDED.note
            """,
            tg_id, role, added_by, note,
        )
    _invalidate_role(tg_id)


async def remove_admin(tg_id: int) -> bool:
    async with pool().acquire() as conn:
        result = await conn.execute("DELETE FROM admins WHERE tg_id = $1", tg_id)
    _invalidate_role(tg_id)
    return result == "DELETE 1"


async def set_role(tg_id: int, role: str) -> bool:
    async with pool().acquire() as conn:
        result = await conn.execute(
            "UPDATE admins SET role = $2 WHERE tg_id = $1", tg_id, role
        )
    _invalidate_role(tg_id)
    return result == "UPDATE 1"


async def set_note(tg_id: int, note: str | None) -> None:
    async with pool().acquire() as conn:
        await conn.execute("UPDATE admins SET note = $2 WHERE tg_id = $1", tg_id, note)


# --- Scope: shop_assignments ---

async def assigned_shop_ids(admin_tg_id: int) -> list[int]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT shop_id FROM shop_assignments WHERE admin_tg_id = $1",
            admin_tg_id,
        )
    return [r["shop_id"] for r in rows]


async def assign_shop(shop_id: int, admin_tg_id: int, by: int) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO shop_assignments (shop_id, admin_tg_id, assigned_by)
            VALUES ($1, $2, $3) ON CONFLICT DO NOTHING
            """,
            shop_id, admin_tg_id, by,
        )


async def unassign_shop(shop_id: int, admin_tg_id: int) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            "DELETE FROM shop_assignments WHERE shop_id = $1 AND admin_tg_id = $2",
            shop_id, admin_tg_id,
        )


async def set_admin_assignments(admin_tg_id: int, shop_ids: list[int], by: int) -> None:
    async with pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM shop_assignments WHERE admin_tg_id = $1", admin_tg_id
            )
            for sid in shop_ids:
                await conn.execute(
                    """
                    INSERT INTO shop_assignments (shop_id, admin_tg_id, assigned_by)
                    VALUES ($1, $2, $3)
                    """,
                    sid, admin_tg_id, by,
                )


async def shop_admins(shop_id: int) -> list[int]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT admin_tg_id FROM shop_assignments WHERE shop_id = $1",
            shop_id,
        )
    return [r["admin_tg_id"] for r in rows]


async def visible_shop_ids(actor_tg_id: int) -> list[int] | None:
    """None — без ограничений (super_admin или не админ).
    Список ID — только эти магазины доступны (обычный admin)."""
    role = await get_role(actor_tg_id)
    if role == "super_admin":
        return None
    if role == "admin":
        return await assigned_shop_ids(actor_tg_id)
    return []


async def can_access_shop(actor_tg_id: int, shop_id: int) -> bool:
    role = await get_role(actor_tg_id)
    if role == "super_admin":
        return True
    if role != "admin":
        return False
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM shop_assignments WHERE admin_tg_id = $1 AND shop_id = $2",
            actor_tg_id, shop_id,
        )
    return row is not None
