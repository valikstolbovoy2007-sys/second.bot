"""Repo for `shop_photos` (gallery of photos attached to a shop)."""
from data.db import pool


async def list_photos(shop_id: int) -> list[dict]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, file_id, ord, uploaded_by, uploaded_at
            FROM shop_photos
            WHERE shop_id = $1
            ORDER BY ord, id
            """,
            shop_id,
        )
    return [dict(r) for r in rows]


async def add_photo(shop_id: int, file_id: str, uploaded_by: int) -> int:
    async with pool().acquire() as conn:
        next_ord = await conn.fetchval(
            "SELECT COALESCE(MAX(ord), -1) + 1 FROM shop_photos WHERE shop_id = $1",
            shop_id,
        )
        row = await conn.fetchrow(
            """
            INSERT INTO shop_photos (shop_id, file_id, ord, uploaded_by)
            VALUES ($1, $2, $3, $4) RETURNING id
            """,
            shop_id, file_id, int(next_ord or 0), uploaded_by,
        )
    return int(row["id"])


async def remove_photo(photo_id: int) -> bool:
    async with pool().acquire() as conn:
        result = await conn.execute(
            "DELETE FROM shop_photos WHERE id = $1",
            photo_id,
        )
    return result == "DELETE 1"


async def get_photo(photo_id: int) -> dict | None:
    async with pool().acquire() as conn:
        r = await conn.fetchrow(
            """
            SELECT id, shop_id, file_id, ord
            FROM shop_photos WHERE id = $1
            """,
            photo_id,
        )
    return dict(r) if r else None


async def count_photos(shop_id: int) -> int:
    async with pool().acquire() as conn:
        n = await conn.fetchval(
            "SELECT COUNT(*) FROM shop_photos WHERE shop_id = $1",
            shop_id,
        )
    return int(n or 0)
