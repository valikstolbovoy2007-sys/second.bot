from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from data.repos.admin_roles import get_role, is_super_admin


class IsAdmin(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        return await get_role(event.from_user.id) is not None


class IsSuperAdmin(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        return await is_super_admin(event.from_user.id)
