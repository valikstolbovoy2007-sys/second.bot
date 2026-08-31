"""Broadcast lifecycle service.

Handles enqueueing, dispatching, pausing and cancelling broadcasts. The
dispatch loop is started by `start_dispatcher` once at bot startup and runs
forever. Pending broadcasts are picked up either immediately or when their
`scheduled_at` time has passed.
"""
import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from aiogram import Bot
from aiogram.exceptions import (
    TelegramForbiddenError,
    TelegramNotFound,
    TelegramRetryAfter,
)

from data.db import pool

log = logging.getLogger(__name__)

PROGRESS_BATCH = 25  # check status / persist progress every N sends
SEND_GAP_SEC = 0.04


@dataclass
class BroadcastRow:
    id: int
    payload: dict
    audience_filter: dict
    scheduled_at: datetime | None
    status: str
    sent: int
    failed: int
    created_by: int


def _row(r) -> BroadcastRow:
    payload = r["payload"]
    audience = r["audience_filter"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    if isinstance(audience, str):
        audience = json.loads(audience)
    return BroadcastRow(
        id=r["id"],
        payload=payload or {},
        audience_filter=audience or {},
        scheduled_at=r["scheduled_at"],
        status=r["status"],
        sent=r["sent"],
        failed=r["failed"],
        created_by=r["created_by"],
    )


async def enqueue(
    *,
    payload: dict[str, Any],
    audience_filter: dict[str, Any],
    created_by: int,
    scheduled_at: datetime | None = None,
) -> int:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO broadcasts (payload, audience_filter, scheduled_at, status, created_by)
            VALUES ($1::jsonb, $2::jsonb, $3, 'pending', $4)
            RETURNING id
            """,
            json.dumps(payload, ensure_ascii=False),
            json.dumps(audience_filter, ensure_ascii=False),
            scheduled_at,
            created_by,
        )
    return int(row["id"])


async def get(bc_id: int) -> BroadcastRow | None:
    async with pool().acquire() as conn:
        r = await conn.fetchrow(
            "SELECT * FROM broadcasts WHERE id = $1",
            bc_id,
        )
    return _row(r) if r else None


async def count_recent_by_actor(actor_tg_id: int, hours: int = 24) -> int:
    """Count non-cancelled broadcasts created by `actor_tg_id` in the last N hours."""
    async with pool().acquire() as conn:
        n = await conn.fetchval(
            """
            SELECT COUNT(*) FROM broadcasts
            WHERE created_by = $1
              AND status <> 'cancelled'
              AND created_at >= now() - ($2::int * INTERVAL '1 hour')
            """,
            actor_tg_id, hours,
        )
    return int(n or 0)


async def list_recent(limit: int = 20, offset: int = 0) -> list[BroadcastRow]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM broadcasts
            ORDER BY created_at DESC
            LIMIT $1 OFFSET $2
            """,
            limit, offset,
        )
    return [_row(r) for r in rows]


async def set_status(bc_id: int, status: str) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            "UPDATE broadcasts SET status = $1 WHERE id = $2",
            status, bc_id,
        )


async def request_cancel(bc_id: int) -> None:
    """Marks a broadcast as cancelled.

    If the dispatcher is currently sending it, it will pick up the new status
    on the next progress checkpoint and stop.
    """
    async with pool().acquire() as conn:
        await conn.execute(
            "UPDATE broadcasts SET status = 'cancelled', finished_at = now() WHERE id = $1 AND status IN ('pending','running','paused')",
            bc_id,
        )


async def request_pause(bc_id: int) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            "UPDATE broadcasts SET status = 'paused' WHERE id = $1 AND status = 'running'",
            bc_id,
        )


async def request_resume(bc_id: int) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            "UPDATE broadcasts SET status = 'pending' WHERE id = $1 AND status = 'paused'",
            bc_id,
        )


async def resolve_audience(actor_tg_id: int, audience: dict) -> list[int]:
    """Resolve an audience filter to a list of tg_ids.

    Filters the list against `user_blocks` and `users.is_blocked` so that
    blocked recipients never receive the broadcast.

    Supported kinds:
      - all                       — every active, unblocked user
      - subscribers               — users subscribed to specific shop_ids
                                    (audience["shop_ids"] = [int, ...])
      - chain                     — users subscribed to any shop in chain_name
      - active_recent             — created_at >= now - INTERVAL '<days> days'
    """
    kind = audience.get("kind", "all")
    async with pool().acquire() as conn:
        if kind == "all":
            rows = await conn.fetch(
                """
                SELECT u.tg_id FROM users u
                LEFT JOIN user_blocks b ON b.tg_id = u.tg_id
                WHERE u.is_blocked = false AND b.tg_id IS NULL
                """
            )
        elif kind == "subscribers":
            shop_ids = audience.get("shop_ids") or []
            if not shop_ids:
                return []
            rows = await conn.fetch(
                """
                SELECT DISTINCT u.tg_id FROM users u
                JOIN subscriptions s ON s.user_id = u.id
                LEFT JOIN user_blocks b ON b.tg_id = u.tg_id
                WHERE s.shop_id = ANY($1::int[])
                  AND u.is_blocked = false AND b.tg_id IS NULL
                """,
                shop_ids,
            )
        elif kind == "chain":
            chain = audience.get("chain")
            if not chain:
                return []
            rows = await conn.fetch(
                """
                SELECT DISTINCT u.tg_id FROM users u
                JOIN subscriptions sub ON sub.user_id = u.id
                JOIN shops s ON s.id = sub.shop_id
                LEFT JOIN user_blocks b ON b.tg_id = u.tg_id
                WHERE s.chain_name = $1
                  AND u.is_blocked = false AND b.tg_id IS NULL
                """,
                chain,
            )
        elif kind == "active_recent":
            try:
                days = int(audience.get("days", 30))
            except (TypeError, ValueError):
                days = 30
            rows = await conn.fetch(
                """
                SELECT u.tg_id FROM users u
                LEFT JOIN user_blocks b ON b.tg_id = u.tg_id
                WHERE u.is_blocked = false AND b.tg_id IS NULL
                  AND u.created_at >= now() - ($1::int * INTERVAL '1 day')
                """,
                days,
            )
        elif kind == "list":
            tg_ids = audience.get("tg_ids") or []
            if not tg_ids:
                return []
            rows = await conn.fetch(
                """
                SELECT u.tg_id FROM users u
                LEFT JOIN user_blocks b ON b.tg_id = u.tg_id
                WHERE u.tg_id = ANY($1::bigint[])
                  AND u.is_blocked = false AND b.tg_id IS NULL
                """,
                tg_ids,
            )
        else:
            return []
    return [int(r["tg_id"]) for r in rows]


async def _send_payload(bot: Bot, tg_id: int, payload: dict) -> None:
    text = payload.get("text") or ""
    photo = payload.get("photo")
    document = payload.get("document")
    if photo:
        await bot.send_photo(tg_id, photo, caption=text or None)
    elif document:
        await bot.send_document(tg_id, document, caption=text or None)
    else:
        await bot.send_message(tg_id, text)


async def _is_cancelled_or_paused(bc_id: int) -> str:
    async with pool().acquire() as conn:
        r = await conn.fetchval(
            "SELECT status FROM broadcasts WHERE id = $1", bc_id
        )
    return str(r or "")


async def _persist_progress(bc_id: int, sent: int, failed: int) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            "UPDATE broadcasts SET sent = $2, failed = $3 WHERE id = $1",
            bc_id, sent, failed,
        )


async def _dispatch_one(bot: Bot, bc: BroadcastRow) -> None:
    log.info("dispatching broadcast id=%s kind=%s", bc.id, bc.audience_filter.get("kind"))
    async with pool().acquire() as conn:
        await conn.execute(
            "UPDATE broadcasts SET status='running', started_at = COALESCE(started_at, now()) WHERE id = $1",
            bc.id,
        )

    tg_ids = await resolve_audience(bc.created_by, bc.audience_filter)
    sent = bc.sent
    failed = bc.failed

    for i, tg_id in enumerate(tg_ids[sent + failed:], start=1):
        try:
            await _send_payload(bot, tg_id, bc.payload)
            sent += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                await _send_payload(bot, tg_id, bc.payload)
                sent += 1
            except Exception:
                failed += 1
        except (TelegramForbiddenError, TelegramNotFound):
            failed += 1
        except Exception:
            log.exception("broadcast %s send failed for %s", bc.id, tg_id)
            failed += 1

        if i % PROGRESS_BATCH == 0:
            await _persist_progress(bc.id, sent, failed)
            status = await _is_cancelled_or_paused(bc.id)
            if status in {"cancelled", "paused"}:
                log.info("broadcast %s halted with status=%s", bc.id, status)
                async with pool().acquire() as conn:
                    if status == "cancelled":
                        await conn.execute(
                            "UPDATE broadcasts SET sent=$2, failed=$3, finished_at=now() WHERE id=$1",
                            bc.id, sent, failed,
                        )
                return

        await asyncio.sleep(SEND_GAP_SEC)

    async with pool().acquire() as conn:
        await conn.execute(
            "UPDATE broadcasts SET sent=$2, failed=$3, status='done', finished_at=now() WHERE id=$1",
            bc.id, sent, failed,
        )


async def _dispatcher_tick(bot: Bot) -> None:
    """Pick up to one ready broadcast and process it.

    Picks `pending` broadcasts whose scheduled_at is null or already past.
    """
    now = datetime.now(timezone.utc)
    async with pool().acquire() as conn:
        r = await conn.fetchrow(
            """
            SELECT * FROM broadcasts
            WHERE status = 'pending'
              AND (scheduled_at IS NULL OR scheduled_at <= $1)
            ORDER BY COALESCE(scheduled_at, created_at) ASC
            LIMIT 1
            """,
            now,
        )
    if not r:
        return
    bc = _row(r)
    try:
        await _dispatch_one(bot, bc)
    except Exception:
        log.exception("dispatcher failed for broadcast %s", bc.id)
        async with pool().acquire() as conn:
            await conn.execute(
                "UPDATE broadcasts SET status='failed', finished_at=now() WHERE id=$1",
                bc.id,
            )


_dispatcher_task: asyncio.Task | None = None


async def start_dispatcher(bot: Bot) -> None:
    """Start the in-process broadcast dispatcher.

    Idempotent: calling it again has no effect if a task is already running.
    """
    global _dispatcher_task
    if _dispatcher_task and not _dispatcher_task.done():
        return

    async def _loop() -> None:
        log.info("broadcast dispatcher started")
        while True:
            try:
                await _dispatcher_tick(bot)
            except Exception:
                log.exception("dispatcher tick crashed")
            await asyncio.sleep(5)

    _dispatcher_task = asyncio.create_task(_loop(), name="broadcast_dispatcher")


async def stop_dispatcher() -> None:
    global _dispatcher_task
    if _dispatcher_task and not _dispatcher_task.done():
        _dispatcher_task.cancel()
        try:
            await _dispatcher_task
        except asyncio.CancelledError:
            pass
    _dispatcher_task = None
