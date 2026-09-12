"""Per-user "workspace" tracking for smooth catalog navigation.

Список магазинов живёт в своём сообщении и никогда не удаляется:
при открытии карточки она отправляется отдельным сообщением (режим
«панель»), а «Назад» просто закрывает карточку и перерисовывает список
на месте. Так у потребителя не мелькает «удаление всего меню».
"""


class Workspace:
    def __init__(self) -> None:
        self._home: dict[int, tuple[int, int]] = {}
        self._card: dict[int, int] = {}

    def set_home(self, user_id: int, chat_id: int, message_id: int) -> None:
        self._home[user_id] = (chat_id, message_id)

    def home(self, user_id: int) -> tuple[int, int] | None:
        return self._home.get(user_id)

    def open_card(self, user_id: int, message_id: int) -> None:
        self._card[user_id] = message_id

    def card(self, user_id: int) -> int | None:
        return self._card.get(user_id)

    def close_card(self, user_id: int) -> None:
        self._card.pop(user_id, None)


ws = Workspace()