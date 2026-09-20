from aiogram import F, Router
from aiogram.types import CallbackQuery

from data.repos.users import is_admin
from keyboards.main_kb import main_menu
from services.chat_render import render
from services.texts import t
from services.workspace import ws

router = Router(name="donate")


@router.callback_query(F.data == "donate:open")
async def cb_donate_open(call: CallbackQuery) -> None:
    """Экран «Пожертвования на хостинг» с кнопкой «🏠 В меню»."""
    admin = await is_admin(call.from_user.id)
    new_id = await render(
        call.bot, call.message.chat.id, call.message.message_id,
        await t("donate.text"),
        await main_menu(is_admin=admin),
    )
    ws.set_active(call.from_user.id, new_id)
    await call.answer()