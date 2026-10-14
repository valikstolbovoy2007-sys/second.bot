import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from data.repos.admin_roles import get_role
from handlers.admin.feedback import FbCb
from handlers.admin.filters import IsAdmin
from handlers.admin.shops import ShopCb
from handlers.admin.ui import safe_edit
from services.chat_render import render_focus

log = logging.getLogger(__name__)
router = Router(name="admin_panel")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def panel_kb(is_super: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="🛍 Мои магазины", callback_data=ShopCb(action="list", page=0).pack())],
        [InlineKeyboardButton(text="📦 Завозы", callback_data="adm:arr:menu")],
        [InlineKeyboardButton(text="📣 Рассылка", callback_data="adm:bc:menu")],
    ]
    if is_super:
        rows.append([InlineKeyboardButton(text="💬 Фидбек", callback_data=FbCb(action="list", page=0).pack())])
        rows.append([InlineKeyboardButton(text="📊 Статистика", callback_data="adm:stats")])
        rows.append([InlineKeyboardButton(text="👥 Админы и магазины", callback_data="sa:admins:list:0")])
        rows.append([InlineKeyboardButton(text="👥 Все пользователи", callback_data="sa:users:list")])
        rows.append([InlineKeyboardButton(text="🎨 Шаблон карточки", callback_data="sa:tpl:view")])
        rows.append([InlineKeyboardButton(text="📝 Тексты бота", callback_data="sa:texts")])
        rows.append([InlineKeyboardButton(text="🛠 Режим обслуживания", callback_data="sa:maint:menu")])
    rows.append([InlineKeyboardButton(text="🏠 В меню", callback_data="menu:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def panel_text(role: str) -> str:
    if role == "super_admin":
        return (
            "🛡 <b>Супер-админ</b>\n\n"
            "Все функции — в этом окне, без лишних переходов:\n"
            "• 🛍 магазины: создать/удалить — «Мои магазины» → внизу «🆕 Добавить магазин»\n"
            "• 👥 админы и назначение им магазинов, все пользователи\n"
            "• 💬 фидбек, 📊 статистика, 📣 рассылки\n"
            "• 🎨 шаблон карточки, 📝 тексты бота, 🛠 режим обслуживания"
        )
    return (
        "🛠 <b>Админ</b>\n\n"
        "Ты управляешь только своими магазинами:\n"
        "• 🛍 редактируешь карточку (имя, адрес, описание, цикл, цены)\n"
        "• 📦 ставишь даты завозов\n"
        "• 📣 рассылаешь сообщения подписчикам своих магазинов\n\n"
        "<i>Если нужны новые магазины или другие права — попроси супер-админа.</i>"
    )


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    await state.clear()
    role = await get_role(message.from_user.id) or "admin"
    await render_focus(
        message.bot, message,
        panel_text(role), panel_kb(role == "super_admin"),
    )


async def show_panel(call: CallbackQuery, state: FSMContext) -> None:
    """Перерисовать админ-панель на сообщении колбэка (возврат из подэкран)."""
    await state.clear()
    role = await get_role(call.from_user.id) or "admin"
    await safe_edit(call, panel_text(role), panel_kb(role == "super_admin"))
    await call.answer()


@router.callback_query(F.data == "admin:open")
async def cb_admin_open(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    role = await get_role(call.from_user.id) or "admin"
    await safe_edit(call, panel_text(role), panel_kb(role == "super_admin"))
    await call.answer()


@router.callback_query(F.data == "adm:back")
async def cb_admin_back(call: CallbackQuery, state: FSMContext) -> None:
    await show_panel(call, state)
