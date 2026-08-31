import json
import logging
from typing import Any

from data.db import pool

log = logging.getLogger("audit")


async def write(
    actor_tg_id: int | None,
    action: str,
    target_type: str | None = None,
    target_id: str | int | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    try:
        async with pool().acquire() as conn:
            await conn.execute(
                """
                INSERT INTO audit_log (actor_tg_id, action, target_type, target_id, payload)
                VALUES ($1, $2, $3, $4, $5)
                """,
                actor_tg_id,
                action,
                target_type,
                str(target_id) if target_id is not None else None,
                json.dumps(payload, default=str, ensure_ascii=False) if payload else None,
            )
    except Exception:
        log.exception("audit write failed action=%s", action)


async def list_recent(
    limit: int = 50,
    offset: int = 0,
    actor_tg_id: int | None = None,
    action_like: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    days: int | None = None,
) -> list[dict]:
    """List audit entries with optional filters.

    `action_like` is a substring match (ILIKE).
    `days` filters to last N days based on `ts`.
    """
    conds: list[str] = []
    args: list = [limit, offset]
    idx = 3
    if actor_tg_id:
        conds.append(f"actor_tg_id = ${idx}")
        args.append(actor_tg_id)
        idx += 1
    if action_like:
        conds.append(f"action ILIKE ${idx}")
        args.append(f"%{action_like}%")
        idx += 1
    if target_type:
        conds.append(f"target_type = ${idx}")
        args.append(target_type)
        idx += 1
    if target_id is not None:
        conds.append(f"target_id = ${idx}")
        args.append(str(target_id))
        idx += 1
    if days and days > 0:
        conds.append(f"ts >= now() - (${idx}::int * INTERVAL '1 day')")
        args.append(days)
        idx += 1
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, ts, actor_tg_id, action, target_type, target_id, payload
            FROM audit_log
            {where}
            ORDER BY ts DESC
            LIMIT $1 OFFSET $2
            """,
            *args,
        )
    return [dict(r) for r in rows]


async def distinct_target_types(limit: int = 30) -> list[str]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT target_type, COUNT(*) AS n FROM audit_log
            WHERE target_type IS NOT NULL
            GROUP BY target_type ORDER BY n DESC LIMIT $1
            """,
            limit,
        )
    return [r["target_type"] for r in rows]
