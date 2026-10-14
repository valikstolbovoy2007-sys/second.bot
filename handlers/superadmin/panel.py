import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from handlers.admin.filters import IsSuperAdmin
from handlers.admin.panel import panel_kb, panel_text, show_panel
from handlers.admin.ui import render_screen_msg

log = logging.getLogger(__name__)
router = Router(name="sa_panel")
router.message.filter(IsSuperAdmin())
router.callback_query.filter(IsSuperAdmin())


@router.message(Command("sudo"))
async def cmd_sudo(message: Message, state: FSMContext) -> None:
    await state.clear()
    await render_screen_msg(message, state, panel_text("super_admin"), panel_kb(True))


@router.callback_query(F.data == "sa:menu")
async def cb_menu(call: CallbackQuery, state: FSMContext) -> None:
    """Возврат из подэкранов супер-админки — на главное окно (админ-панель)."""
    await show_panel(call, state)
