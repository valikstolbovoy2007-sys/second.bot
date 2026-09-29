"""Per-chat journal of message ids the bot has sent.

Telegram message ids increase monotonically inside a chat, so any bot message
posted after the current menu has a *larger* id. That lets the render layer
detect when a menu the user is interacting with got "interrupted" by a newer
bot message below it (delivery reminder, broadcast, admin note, …) and
teleport the menu to the bottom of the chat on the next button press.

The journal is mirrored to the DB (see services.journal_persist) so that a
bot restart doesn't blind the teleport decision.
"""
import threading

_MAX_PER_CHAT = 64


class ChatJournal:
    def __init__(self) -> None:
        self._edges: dict[int, list[int]] = {}
        self._dirty: set[int] = set()
        self._lock = threading.Lock()

    def record(self, chat_id: int, message_id: int) -> None:
        with self._lock:
            ids = self._edges.setdefault(chat_id, [])
            ids.append(message_id)
            self._dirty.add(chat_id)
            if len(ids) > _MAX_PER_CHAT:
                del ids[: len(ids) - _MAX_PER_CHAT]

    def has_newer(self, chat_id: int, message_id: int) -> bool:
        with self._lock:
            return any(mid > message_id for mid in self._edges.get(chat_id, ()))

    def drop_below(self, chat_id: int, message_id: int) -> None:
        """Forget ids that are now irrelevant after a teleport."""
        with self._lock:
            ids = self._edges.get(chat_id)
            if not ids:
                return
            trimmed = [mid for mid in ids if mid > message_id]
            if len(trimmed) != len(ids):
                self._edges[chat_id] = trimmed
                self._dirty.add(chat_id)

    def remove(self, chat_id: int, message_id: int) -> None:
        """Forget an id whose message got deleted (prevents phantom teleports)."""
        with self._lock:
            ids = self._edges.get(chat_id)
            if not ids:
                return
            kept = [mid for mid in ids if mid != message_id]
            if len(kept) != len(ids):
                self._edges[chat_id] = kept
                self._dirty.add(chat_id)

    def load(self, chat_id: int, message_ids: list[int]) -> None:
        """Restore a chat's journal after a process restart."""
        with self._lock:
            ids = sorted(set(message_ids))[-_MAX_PER_CHAT:]
            if ids:
                self._edges[chat_id] = ids
            else:
                self._edges.pop(chat_id, None)

    def dirty_snapshot(self) -> dict[int, list[int]]:
        """Return dirty chats' ids (incl. empty ones, meaning "forget all") and
        clear the dirty flags (for persisting)."""
        with self._lock:
            if not self._dirty:
                return {}
            out = {}
            for c in self._dirty:
                ids = self._edges.get(c)
                out[c] = list(ids) if ids else []
            self._dirty = set()
            return out

    def mark_dirty(self, chat_id: int) -> None:
        with self._lock:
            self._dirty.add(chat_id)


journal = ChatJournal()