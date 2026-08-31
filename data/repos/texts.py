"""CRUD for bot_texts table — overridden values only.
Defaults live in services/texts.py."""
from __future__ import annotations

from datetime import datetime

from data.db import pool


async def get_override(key: str) -> str | None:
    async with pool().acquire() as conn:
        row = await conn.fetchrow("SELECT value FROM bot_texts WHERE key = $1", key)
    return row["value"] if row else None


async def set_override(key: str, value: str, by: int | None) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO bot_texts (key, value, updated_by, updated_at)
            VALUES ($1, $2, $3, now())
            ON CONFLICT (key) DO UPDATE SET
              value = EXCLUDED.value,
              updated_by = EXCLUDED.updated_by,
              updated_at = now()
            """,
            key, value, by,
        )


async def delete_override(key: str) -> bool:
    async with pool().acquire() as conn:
        result = await conn.execute("DELETE FROM bot_texts WHERE key = $1", key)
    return result == "DELETE 1"


async def list_overridden_keys() -> set[str]:
    async with pool().acquire() as conn:
        rows = await conn.fetch("SELECT key FROM bot_texts")
    return {r["key"] for r in rows}


async def get_meta(key: str) -> tuple[datetime, int | None] | None:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT updated_at, updated_by FROM bot_texts WHERE key = $1", key
        )
    return (row["updated_at"], row["updated_by"]) if row else None
