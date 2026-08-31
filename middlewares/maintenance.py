from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from data.repos.admin_roles import is_admin
from services.config_live import get as cfg_get, set_value as cfg_set


async def _maintenance_active() -> bool:
    on = bool(await cfg_get("maintenance_mode", False))
    if not on:
        return False
    until_raw = await cfg_get("maintenance_until", None)
    if not until_raw:
        return True
    try:
        until = datetime.fromisoformat(until_raw)
    except ValueError:
        return True
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) >= until:
        await cfg_set("maintenance_mode", "0", "bool", "Maintenance mode", None)
        await cfg_set("maintenance_until", None, "str",
                      "Maintenance auto-off timestamp (ISO)", None)
        return False
    return True


class MaintenanceMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not await _maintenance_active():
            return await handler(event, data)

        user = getattr(event, "from_user", None)
        if user and await is_admin(user.id):
            return await handler(event, data)

        msg = await cfg_get("maintenance_message", "🛠 Бот на обслуживании, попробуй позже.")
        if isinstance(event, Message):
            await event.answer(msg)
        elif isinstance(event, CallbackQuery):
            await event.answer(msg, show_alert=True)
        return None
