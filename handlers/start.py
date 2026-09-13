import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from data.repos.users import is_admin, upsert_user
from keyboards.main_kb import main_menu
from services.chat_render import render
from services.texts import t
from services.workspace import ws

log = logging.getLogger(__name__)
router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await upsert_user(message.from_user.id, message.from_user.username)
    log.info("user %s started", message.from_user.id)
    admin = await is_admin(message.from_user.id)
    await state.clear()
    # Команда сама становится экраном: удаляем ввод и рисуем меню ниже.
    await render(
        message.bot, message.chat.id, message.message_id,
        await t("start.welcome"), await main_menu(is_admin=admin),
    )


@router.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext) -> None:
    admin = await is_admin(message.from_user.id)
    await state.clear()
    new_id = await render(
        message.bot, message.chat.id, message.message_id,
        await t("help.text"), await main_menu(is_admin=admin),
    )
    ws.open_help(message.from_user.id, new_id)


@router.callback_query(F.data == "help:open")
async def cb_help(call: CallbackQuery) -> None:
    admin = await is_admin(call.from_user.id)
    new_id = await render(
        call.bot, call.message.chat.id, call.message.message_id,
        await t("help.text"),
        await main_menu(is_admin=admin),
    )
    ws.open_help(call.from_user.id, new_id)
    await call.answer()


@router.callback_query(F.data.in_({"menu", "menu:open"}))
async def cb_menu(call: CallbackQuery) -> None:
    admin = await is_admin(call.from_user.id)
    text = await t("start.welcome")
    kb = await main_menu(is_admin=admin)
    ws.pop_help(call.from_user.id)

    if call.message.photo:
        # Фото-сообщение (карточка) нельзя превратить в текстовое меню на
        # месте: сначала шлём меню, затем удаляем карточку (без «провала»).
        await call.bot.send_message(chat_id=call.from_user.id, text=text, reply_markup=kb)
        try:
            await call.message.delete()
        except TelegramBadRequest:
            pass
    else:
        await render(call.bot, call.message.chat.id, call.message.message_id, text, kb)
    await call.answer()