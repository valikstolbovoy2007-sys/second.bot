import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from handlers.admin.filters import IsSuperAdmin
from handlers.admin.ui import safe_edit

log = logging.getLogger(__name__)
router = Router(name="sa_panel")
router.message.filter(IsSuperAdmin())
router.callback_query.filter(IsSuperAdmin())


def _kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Админы и магазины", callback_data="sa:admins:list:0")],
        [InlineKeyboardButton(text="📣 Рассылки", callback_data="adm:bc:menu")],
        [InlineKeyboardButton(text="🎨 Шаблон карточки", callback_data="sa:tpl:view")],
        [InlineKeyboardButton(text="📝 Тексты бота", callback_data="sa:texts")],
        [InlineKeyboardButton(text="🛠 Режим обслуживания", callback_data="sa:maint:menu")],
        [InlineKeyboardButton(text="← В админку", callback_data="adm:back")],
    ])


TEXT = (
    "🛡 <b>Супер-админка</b>\n\n"
    "Только ты можешь:\n"
    "• создавать и удалять магазины (кнопка «🛍 Мои магазины» в админке → "
    "внизу списка «🆕 Добавить магазин»)\n"
    "• назначать админов и магазины им в управление\n"
    "• рассылать сообщения пользователям\n"
    "• менять шаблон карточки магазина\n"
    "• редактировать тексты сообщений и кнопок\n"
    "• включать режим обслуживания"
)


@router.message(Command("sudo"))
async def cmd_sudo(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(TEXT, reply_markup=_kb())


@router.callback_query(F.data == "sa:menu")
async def cb_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await safe_edit(call, TEXT, _kb())
    await call.answer()
