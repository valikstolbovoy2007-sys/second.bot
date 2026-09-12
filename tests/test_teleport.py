"""Teleport semantics for menu rendering.

Правила:
  - если под меню/карточкой нет свежих сообщений бота (журнал пуст) —
    при нажатии кнопки происходит «превращение» на месте (edit), БЕЗ
    удаления и повторной отправки;
  - если под меню появилось сообщение бота (завоз/рассылка и т.п.) —
    меню удаляется и создаётся заново ниже этого сообщения;
  - переход фото↔текст (нет плоского edit из photo в text) делает
    send+delete (сначала новое, потом старое) — это вынужденная навигация,
    а не телепорт. Порядок «сначала новое» убирает визуальный «провал».
"""
import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.exceptions import TelegramBadRequest

from data.repos.shops import Shop
from services.card_view import show_shop_card, show_text_view
from services.chat_journal import ChatJournal
from services.chat_render import render

try:  # aiogram exception message differs across minor versions
    NOT_MODIFIED = str(TelegramBadRequest("x", "Bad Request: message is not modified"))
    NOT_FOUND = str(TelegramBadRequest("x", "Bad Request: message to edit not found"))
except Exception:  # pragma: no cover
    NOT_MODIFIED = "message is not modified"
    NOT_FOUND = "message to edit not found"


class _Res:
    def __init__(self, mid: int) -> None:
        self.message_id = mid


class _Msg:
    def __init__(self, chat_id: int, message_id: int, photo=()) -> None:
        self.chat = SimpleNamespace(id=chat_id)
        self.message_id = message_id
        self.photo = photo
        self.calls: list = []

    async def delete(self) -> None:
        self.calls.append("delete")

    async def answer(self, *a, **kw) -> _Res:
        self.calls.append("answer")
        return _Res(self.message_id + 1000)

    async def answer_photo(self, *a, **kw) -> _Res:
        self.calls.append("answer_photo")
        return _Res(self.message_id + 1000)

    async def edit_text(self, *a, **kw) -> None:
        self.calls.append("edit_text")

    async def edit_media(self, *a, **kw) -> None:
        self.calls.append("edit_media")

    async def edit_reply_markup(self, *a, **kw) -> None:
        self.calls.append("edit_reply_markup")


class _Bot:
    def __init__(self, next_id: int = 10_000) -> None:
        self._next_id = next_id
        self.calls: list = []
        self.edit_error: Exception | None = None

    async def edit_message_text(self, text, chat_id=None, message_id=None, reply_markup=None, **kw) -> None:
        self.calls.append(("edit", chat_id, message_id, text))
        if self.edit_error:
            e, self.edit_error = self.edit_error, None
            raise e

    async def delete_message(self, chat_id, message_id) -> None:
        self.calls.append(("delete", chat_id, message_id))

    async def send_message(self, chat_id, text, reply_markup=None, **kw) -> _Res:
        self.calls.append(("send", chat_id, text))
        msg = _Res(self._next_id)
        self._next_id += 1
        return msg


class _Call:
    def __init__(self, bot: _Bot, message: _Msg) -> None:
        self.bot = bot
        self.message = message

    async def answer(self, *a, **kw) -> None:
        pass

    @property
    def from_user(self) -> SimpleNamespace:
        return SimpleNamespace(id=1, username="tester")


def _shop(**overrides) -> Shop:
    base = dict(
        id=1, name="Сешка", address="ул. Тестовая 1", description=None, chain_name=None,
        cycle_length=14, anchor_date=date(2026, 4, 1), price_start=1200, price_step=80,
        working_hours=None, is_active=True, maps_url=None, monthly_weekday=None,
        monthly_occurrence=1,
    )
    base.update(overrides)
    return Shop(**base)


def _patch(module_name: str, journal: ChatJournal):
    return patch(f"{module_name}.journal", journal)


class TestRender:
    def test_clean_journal_edits_in_place(self) -> None:
        """Без сообщений под меню — превращение на месте, БЕЗ удаления."""
        bot = _Bot()
        journal = ChatJournal()
        with _patch("services.chat_render", journal):
            new_id = asyncio.run(render(bot, 555, 42, "Новое меню", reply_markup=None))
        assert new_id == 42
        assert [c[0] for c in bot.calls] == ["edit"]
        assert not journal.has_newer(555, 42)

    def test_push_below_teleports(self) -> None:
        """Под меню есть сообщение бота → удалить и создать новое."""
        bot = _Bot()
        journal = ChatJournal()
        journal.record(555, 100)  # уведомление, id больше 42
        with _patch("services.chat_render", journal):
            new_id = asyncio.run(render(bot, 555, 42, "Меню", None))
        assert new_id > 100
        kinds = [c[0] for c in bot.calls]
        assert "delete" in kinds and "send" in kinds
        # новое меню зарегистрировано → следующий клик — обычный edit
        bot2 = _Bot()
        with _patch("services.chat_render", journal):
            new_id2 = asyncio.run(render(bot2, 555, new_id, "Меню 2", None))
        assert new_id2 == new_id
        assert [c[0] for c in bot2.calls] == ["edit"]

    def test_not_modified_is_ok_in_place(self) -> None:
        bot = _Bot()
        bot.edit_error = TelegramBadRequest("x", NOT_MODIFIED)
        journal = ChatJournal()
        with _patch("services.chat_render", journal):
            new_id = asyncio.run(render(bot, 555, 7, "То же самое", None))
        assert new_id == 7
        assert [c[0] for c in bot.calls] == ["edit"]

    def test_deleted_message_falls_back_to_teleport(self) -> None:
        bot = _Bot()
        bot.edit_error = TelegramBadRequest("x", NOT_FOUND)
        journal = ChatJournal()
        with _patch("services.chat_render", journal):
            new_id = asyncio.run(render(bot, 555, 7, "Меню", None))
        assert new_id > 7
        kinds = [c[0] for c in bot.calls]
        assert "delete" in kinds and "send" in kinds


class TestShowTextView:
    def test_clean_text_menu_transforms_in_place(self) -> None:
        """Главное требование: без сообщений снизу — обычное превращение."""
        journal = ChatJournal()
        bot = _Bot()
        msg = _Msg(chat_id=555, message_id=42)
        call = _Call(bot, msg)
        with _patch("services.card_view", journal):
            asyncio.run(show_text_view(call, "Каталог", None))
        assert msg.calls == ["edit_text"]
        assert bot.calls == []

    def test_push_below_teleports_text_menu(self) -> None:
        journal = ChatJournal()
        journal.record(555, 100)
        bot = _Bot(next_id=101)
        msg = _Msg(chat_id=555, message_id=42)
        call = _Call(bot, msg)
        with _patch("services.card_view", journal):
            asyncio.run(show_text_view(call, "Каталог", None))
        assert "delete" in msg.calls
        assert "answer" in msg.calls
        assert journal.has_newer(555, 100)  # новое меню записано

    def test_photo_to_text_is_navigation_not_teleport(self) -> None:
        journal = ChatJournal()
        bot = _Bot()
        msg = _Msg(chat_id=555, message_id=42, photo=("p",))
        call = _Call(bot, msg)
        with _patch("services.card_view", journal):
            asyncio.run(show_text_view(call, "Список", None))
        # новый контент сначала отправляется, старое удаляется следом
        assert msg.calls == ["answer", "delete"]
        # журнал пуст → позже клик по этому экрану не телепортнётся
        assert not journal.has_newer(555, 42)


class TestShowShopCard:
    def test_clean_card_without_photo_edits_in_place(self) -> None:
        journal = ChatJournal()
        bot = _Bot()
        msg = _Msg(chat_id=555, message_id=42)
        call = _Call(bot, msg)
        shop = _shop(price_step=None)  # текстовая карточка без фото
        with _patch("services.card_view", journal), \
             patch("services.card_view.format_shop_card", new=AsyncMock(return_value="Карточка")), \
             patch("services.card_view.list_photos", new=AsyncMock(return_value=[])):
            asyncio.run(show_shop_card(call, shop, date(2026, 9, 12), is_tracked=False, kb=None))
        assert msg.calls == ["edit_text"]
        assert bot.calls == []

    def test_clean_card_with_photo_uses_delete_resend(self) -> None:
        """Фото-карточка не превращается на месте (photo↔text) — resend+delete."""
        journal = ChatJournal()
        bot = _Bot()
        msg = _Msg(chat_id=555, message_id=42)
        call = _Call(bot, msg)
        with _patch("services.card_view", journal), \
             patch("services.card_view.format_shop_card", new=AsyncMock(return_value="Карточка")), \
             patch("services.card_view.list_photos", new=AsyncMock(return_value=[{"file_id": "FILE123"}])):
            asyncio.run(show_shop_card(call, _shop(), date(2026, 9, 12), is_tracked=False, kb=None))
        # новое (фото) сначала отправляется, старое удаляется следом
        assert msg.calls == ["answer_photo", "delete"]
        assert not journal.has_newer(555, 42)

    def test_push_below_card_teleports(self) -> None:
        journal = ChatJournal()
        journal.record(555, 100)
        bot = _Bot(next_id=101)
        msg = _Msg(chat_id=555, message_id=42)
        call = _Call(bot, msg)
        with _patch("services.card_view", journal), \
             patch("services.card_view.format_shop_card", new=AsyncMock(return_value="Карточка")), \
             patch("services.card_view.list_photos", new=AsyncMock(return_value=[])):
            asyncio.run(show_shop_card(call, _shop(), date(2026, 9, 12), is_tracked=False, kb=None))
        assert "delete" in msg.calls
        assert "answer" in msg.calls