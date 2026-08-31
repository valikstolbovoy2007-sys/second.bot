"""Runtime config stored in bot_config table, with 60s in-memory cache."""
import logging
import time
from typing import Any

from data.db import pool

log = logging.getLogger(__name__)
_CACHE: dict[str, tuple[float, Any]] = {}
_TTL = 60.0


def _coerce(value: str | None, type_: str):
    if value is None:
        return None
    if type_ == "int":
        return int(value)
    if type_ == "bool":
        return value.lower() in ("1", "true", "yes", "on")
    return value


async def get(key: str, default: Any = None) -> Any:
    now = time.time()
    cached = _CACHE.get(key)
    if cached and now - cached[0] < _TTL:
        return cached[1]
    async with pool().acquire() as conn:
        row = await conn.fetchrow("SELECT value, type FROM bot_config WHERE key = $1", key)
    val = _coerce(row["value"], row["type"]) if row else default
    _CACHE[key] = (now, val)
    return val


async def set_value(key: str, value: str | None, type_: str = "str",
                    description: str | None = None, by: int | None = None) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO bot_config (key, value, type, description, updated_by, updated_at)
            VALUES ($1,$2,$3,$4,$5, now())
            ON CONFLICT (key) DO UPDATE SET
              value = EXCLUDED.value,
              type = EXCLUDED.type,
              description = COALESCE(EXCLUDED.description, bot_config.description),
              updated_by = EXCLUDED.updated_by,
              updated_at = now()
            """,
            key, value, type_, description, by,
        )
    _CACHE.pop(key, None)


async def list_all() -> list[dict]:
    async with pool().acquire() as conn:
        rows = await conn.fetch("SELECT key, value, type, description, updated_at FROM bot_config ORDER BY key")
    return [dict(r) for r in rows]


def invalidate(key: str | None = None) -> None:
    if key is None:
        _CACHE.clear()
    else:
        _CACHE.pop(key, None)
