"""Persist services.chat_journal to the DB across bot restarts.

The in-memory journal decides whether a menu needs to teleport to the bottom
of the chat. If the process restarts mid-session, that knowledge is lost and
menus stop teleporting after a deploy. This module mirrors the journal into
the `chat_journal` table: `load_journal` restores it at startup and a
background task periodically flushes dirty chats (batched, so the hot-path
`record()` stays a plain in-memory append).
"""
import asyncio
import logging
from typing import Any

from data.db import pool
from services.chat_journal import journal

log = logging.getLogger(__name__)

FLUSH_EVERY_SECONDS = 30.0

_task: asyncio.Task | None = None


async def load_journal() -> None:
    """Restore the journal from the DB at startup."""
    try:
        async with pool().acquire() as conn:
            rows = await conn.fetch(
                "SELECT chat_id, message_id FROM chat_journal ORDER BY chat_id, message_id"
            )
    except Exception:
        log.exception("journal load failed (teleport will work fresh)")
        return
    by_chat: dict[int, list[int]] = {}
    for r in rows:
        by_chat.setdefault(int(r["chat_id"]), []).append(int(r["message_id"]))
    for chat_id, ids in by_chat.items():
        journal.load(chat_id, ids)
    if by_chat:
        log.info("journal restored: %d chats", len(by_chat))


async def _flush_dirty() -> None:
    snapshot = journal.dirty_snapshot()
    if not snapshot:
        return
    async with pool().acquire() as conn:
        async with conn.transaction():
            for chat_id, ids in snapshot.items():
                await conn.execute(
                    "DELETE FROM chat_journal WHERE chat_id = $1", chat_id,
                )
                if ids:
                    await conn.executemany(
                        """
                        INSERT INTO chat_journal (chat_id, message_id)
                        VALUES ($1, $2) ON CONFLICT DO NOTHING
                        """,
                        [(chat_id, mid) for mid in ids],
                    )


async def _loop() -> None:
    while True:
        await asyncio.sleep(FLUSH_EVERY_SECONDS)
        try:
            await _flush_dirty()
        except Exception:
            log.exception("journal flush failed")


async def start() -> None:
    """Start the periodic journal flush. Idempotent."""
    global _task
    if _task and not _task.done():
        return
    _task = asyncio.create_task(_loop(), name="journal_persist")
    log.info("journal persistence started")


async def stop() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    _task = None
    try:
        await _flush_dirty()
    except Exception:
        log.exception("journal final flush failed")