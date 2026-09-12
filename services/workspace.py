"""Per-user tracking of the currently open shop card.

Список магазинов и карточка занимают одно сообщение («превращение»):
нажатие на сешку удаляет список и шлёт фото-карточку на его место,
«Назад» в свою очередь удаляет карточку и возвращает список.
Отсюда запоминаем, какое текущее сообщение — карточка, чтобы
отличить «Назад с карточки» от обычной пагинации по списку.
"""


class Workspace:
    def __init__(self) -> None:
        self._card: dict[int, int] = {}

    def open_card(self, user_id: int, message_id: int) -> None:
        self._card[user_id] = message_id

    def card(self, user_id: int) -> int | None:
        return self._card.get(user_id)

    def close_card(self, user_id: int) -> None:
        self._card.pop(user_id, None)


ws = Workspace()