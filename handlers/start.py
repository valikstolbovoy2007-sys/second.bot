import logging
from datetime import date

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from data.repos.shops import get_shop
from data.repos.subs import is_subscribed
from data.repos.users import is_admin, upsert_user
from keyboards.catalog_kb import shop_card_kb
from keyboards.main_kb import main_menu
from services.card_view import send_shop_card
from services.chat_render import render, render_focus
from services.texts import t
from services.workspace import ws

log = logging.getLogger(__name__)
router = Router(name="start")


def _parse_start_payload(message: Message) -> str | None:
    text = message.text or ""
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else None


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    db_user_id = await upsert_user(message.from_user.id, message.from_user.username)
    log.info("user %s started", message.from_user.id)
    admin = await is_admin(message.from_user.id)
    await state.clear()

    payload = _parse_start_payload(message)
    if payload and payload.startswith("shop_"):
        try:
            shop_id = int(payload.split("_", 1)[1])
        except (ValueError, IndexError):
            shop_id = None
        if shop_id:
            shop = await get_shop(shop_id)
            if shop and shop.is_active:
                is_tracked = await is_subscribed(db_user_id, shop.id)
                kb = shop_card_kb(shop.id, is_tracked=is_tracked, src="cat", page=0)
                card_id = await send_shop_card(
                    message.bot, message.chat.id, shop, date.today(),
                    is_tracked=is_tracked, kb=kb,
                )
                try:
                    await message.delete()
                except TelegramBadRequest:
                    pass
                ws.set_active(message.from_user.id, card_id)
                return

    # Обычный /start без payload или с невалидным payload — главное меню.
    await render_focus(
        message.bot, message,
        await t("start.welcome"), await main_menu(is_admin=admin),
    )


@router.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext) -> None:
    admin = await is_admin(message.from_user.id)
    await state.clear()
    await render_focus(
        message.bot, message,
        await t("help.text"), await main_menu(is_admin=admin),
    )


@router.callback_query(F.data == "help:open")
async def cb_help(call: CallbackQuery) -> None:
    admin = await is_admin(call.from_user.id)
    new_id = await render(
        call.bot, call.message.chat.id, call.message.message_id,
        await t("help.text"),
        await main_menu(is_admin=admin),
    )
    ws.set_active(call.from_user.id, new_id)
    await call.answer()


@router.callback_query(F.data.in_({"menu", "menu:open"}))
async def cb_menu(call: CallbackQuery) -> None:
    admin = await is_admin(call.from_user.id)
    text = await t("start.welcome")
    kb = await main_menu(is_admin=admin)

    if call.message.photo:
        # Фото-сообщение (карточка) нельзя превратить в текстовое меню на
        # месте: сначала шлём меню, затем удаляем карточку (без «провала»).
        msg = await call.bot.send_message(chat_id=call.from_user.id, text=text, reply_markup=kb)
        try:
            await call.message.delete()
        except TelegramBadRequest:
            pass
        ws.set_active(call.from_user.id, msg.message_id)
    else:
        new_id = await render(call.bot, call.message.chat.id, call.message.message_id, text, kb)
        ws.set_active(call.from_user.id, new_id)
    await call.answer()
