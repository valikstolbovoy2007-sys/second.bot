import hashlib
import json
import logging
from typing import Any

from data.db import pool

log = logging.getLogger("error_log")


def make_signature(traceback_text: str) -> str:
    """Produce a stable short signature for grouping similar errors.

    Uses the last frame and the exception class — this clumps repeats of the
    same bug while still letting different bugs land in different buckets.
    """
    if not traceback_text:
        return "unknown"
    # last meaningful line is usually "ExceptionClass: message"
    tail = traceback_text.strip().splitlines()[-1] if traceback_text.strip() else ""
    # find last 'File "...", line N' frame
    frame = ""
    for line in reversed(traceback_text.strip().splitlines()):
        ls = line.strip()
        if ls.startswith("File "):
            frame = ls
            break
    base = f"{frame}|{tail}"
    digest = hashlib.sha1(base.encode("utf-8", errors="replace")).hexdigest()[:12]
    return digest


async def write_error(
    *,
    source: str | None,
    message: str,
    traceback_text: str,
    update_payload: dict[str, Any] | None = None,
    level: str = "ERROR",
) -> int | None:
    sig = make_signature(traceback_text)
    try:
        async with pool().acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO error_log (level, source, message, traceback, update_payload, signature)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id
                """,
                level,
                source,
                (message or "")[:1000],
                (traceback_text or "")[:8000],
                json.dumps(update_payload, default=str, ensure_ascii=False) if update_payload else None,
                sig,
            )
            return int(row["id"]) if row else None
    except Exception:
        log.exception("error_log insert failed")
        return None


async def list_groups(limit: int = 20, offset: int = 0, only_unresolved: bool = True) -> list[dict]:
    where = "WHERE resolved = false" if only_unresolved else ""
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT signature,
                   COUNT(*) AS count,
                   MAX(ts) AS last_ts,
                   MIN(ts) AS first_ts,
                   (ARRAY_AGG(message ORDER BY ts DESC))[1] AS last_message,
                   (ARRAY_AGG(id ORDER BY ts DESC))[1] AS last_id
            FROM error_log
            {where}
            GROUP BY signature
            ORDER BY MAX(ts) DESC
            LIMIT $1 OFFSET $2
            """,
            limit,
            offset,
        )
    return [dict(r) for r in rows]


async def get_error(error_id: int) -> dict | None:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, ts, level, source, message, traceback, update_payload, signature, resolved
            FROM error_log
            WHERE id = $1
            """,
            error_id,
        )
    return dict(row) if row else None


async def resolve_signature(signature: str) -> int:
    async with pool().acquire() as conn:
        result = await conn.execute(
            "UPDATE error_log SET resolved = true WHERE signature = $1 AND resolved = false",
            signature,
        )
    try:
        return int(result.split(" ")[-1])
    except Exception:
        return 0


async def list_by_signature(signature: str, limit: int = 10) -> list[dict]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, ts, level, source, message, resolved
            FROM error_log
            WHERE signature = $1
            ORDER BY ts DESC
            LIMIT $2
            """,
            signature,
            limit,
        )
    return [dict(r) for r in rows]
