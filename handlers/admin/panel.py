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

log = logging.getLogger(__name__)
router = Router(name="admin_panel")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def _panel_kb(is_super: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="🛍 Мои магазины", callback_data=ShopCb(action="list", page=0).pack())],
        [InlineKeyboardButton(text="📦 Завозы", callback_data="adm:arr:menu")],
        [InlineKeyboardButton(text="💬 Фидбек", callback_data=FbCb(action="list", page=0).pack())],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="adm:stats")],
    ]
    if is_super:
        rows.append([InlineKeyboardButton(text="🛡 Супер-админ", callback_data="sa:menu")])
    rows.append([InlineKeyboardButton(text="🏠 В меню", callback_data="menu:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _panel_text(role: str) -> str:
    if role == "super_admin":
        return (
            "🛡 <b>Супер-админ</b>\n\n"
            "Здесь видны все магазины. Чтобы попасть в управление ботом "
            "(добавить магазин, назначить админа, рассылки, шаблон карточки) — "
            "жми «🛡 Супер-админ»."
        )
    return (
        "🛠 <b>Админ</b>\n\n"
        "Ты управляешь только своими магазинами:\n"
        "• 🛍 редактируешь карточку (имя, адрес, описание, цикл, цены)\n"
        "• 📦 ставишь даты завозов\n"
        "• 💬 отвечаешь на фидбек по своим магазинам\n"
        "• 📊 смотришь статистику по своим магазинам\n\n"
        "<i>Если нужны новые магазины или другие права — попроси супер-админа.</i>"
    )


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    await state.clear()
    role = await get_role(message.from_user.id) or "admin"
    await message.answer(_panel_text(role), reply_markup=_panel_kb(role == "super_admin"))


@router.callback_query(F.data == "admin:open")
async def cb_admin_open(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    role = await get_role(call.from_user.id) or "admin"
    await safe_edit(call, _panel_text(role), _panel_kb(role == "super_admin"))
    await call.answer()


@router.callback_query(F.data == "adm:back")
async def cb_admin_back(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    role = await get_role(call.from_user.id) or "admin"
    await safe_edit(call, _panel_text(role), _panel_kb(role == "super_admin"))
    await call.answer()
