"""Записывает входящие сообщения пользователей в журнал чата.

Рендер меню решает о «телепорте» на основе журнала (services.chat_journal):
если ниже меню есть более свежее сообщение бота — кнопка перерисовывает
меню в конец чата. Но про сообщения самого пользователя журнал не знал,
поэтому если юзер написал что-то ниже меню и нажал кнопку — меню оставалось
на месте. Этот middleware закрывает дыру: любое входящее сообщение в ЛС
(кроме команд — их бот сам удаляет) отмечается в журнале.
"""
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from services.chat_journal import journal


class JournalMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            chat = event.chat
            if chat.type == "private":
                text = event.text or ""
                # Команды бот сам удаляет после обработки — не «засоряем» ими
                # журнал, иначе следующий клик по меню телепортнётся зря.
                if not text.startswith("/"):
                    journal.record(chat.id, event.message_id)
        return await handler(event, data)