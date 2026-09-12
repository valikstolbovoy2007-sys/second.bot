"""Per-chat journal of message ids the bot has sent.

Telegram message ids increase monotonically inside a chat, so any bot message
posted after the current menu has a *larger* id. That lets the render layer
detect when a menu the user is interacting with got "interrupted" by a newer
bot message below it (delivery reminder, broadcast, admin note, …) and
teleport the menu to the bottom of the chat on the next button press.
"""
import threading

_MAX_PER_CHAT = 64


class ChatJournal:
    def __init__(self) -> None:
        self._edges: dict[int, list[int]] = {}
        self._lock = threading.Lock()

    def record(self, chat_id: int, message_id: int) -> None:
        with self._lock:
            ids = self._edges.setdefault(chat_id, [])
            ids.append(message_id)
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
            self._edges[chat_id] = [mid for mid in ids if mid > message_id]


journal = ChatJournal()