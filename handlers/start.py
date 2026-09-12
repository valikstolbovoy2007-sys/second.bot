import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from data.repos.users import is_admin, upsert_user
from keyboards.main_kb import main_menu
from services.chat_journal import journal
from services.chat_render import render
from services.texts import t

log = logging.getLogger(__name__)
router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await upsert_user(message.from_user.id, message.from_user.username)
    log.info("user %s started", message.from_user.id)
    admin = await is_admin(message.from_user.id)
    msg = await message.answer(await t("start.welcome"), reply_markup=await main_menu(is_admin=admin))
    journal.record(message.chat.id, msg.message_id)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    admin = await is_admin(message.from_user.id)
    msg = await message.answer(await t("help.text"), reply_markup=await main_menu(is_admin=admin))
    journal.record(message.chat.id, msg.message_id)


@router.callback_query(F.data == "help:open")
async def cb_help(call: CallbackQuery) -> None:
    admin = await is_admin(call.from_user.id)
    await render(
        call.bot, call.message.chat.id, call.message.message_id,
        await t("help.text"),
        await main_menu(is_admin=admin),
    )
    await call.answer()


@router.callback_query(F.data.in_({"menu", "menu:open"}))
async def cb_menu(call: CallbackQuery) -> None:
    admin = await is_admin(call.from_user.id)

    await call.message.delete()

    msg = await call.bot.send_message(
        chat_id=call.from_user.id,
        text=await t("start.welcome"),
        reply_markup=await main_menu(is_admin=admin),
    )
    journal.record(call.message.chat.id, msg.message_id)

    await call.answer()