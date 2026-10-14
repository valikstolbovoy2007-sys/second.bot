"""Вопрос сешке: кнопка на карточке магазина.

Вопрос уходит админам сешки (обычным, без супер-админов) в ЛС бота —
они отвечают прямо оттуда; супер-админу — в админ-чат только для просмотра.
Флоу зеркалит «Исправить неточность»: прашивание → превью → карточка сешки.
"""
import html
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import settings
from data.repos.admin_roles import shop_regular_admins
from data.repos.questions_repo import save_question
from data.repos.shops import get_shop
from data.repos.users import upsert_user
from keyboards.catalog_kb import CatalogCb
from services.card_view import show_text_view
from services.chat_journal import journal
from services.chat_render import render
from services.shop_card_flow import redraw_shop_card, restore_card_fresh
from services.workspace import ws
from states.question_states import QuestionStates

log = logging.getLogger(__name__)
router = Router(name="questions")

_ZWJ = "\u200d"
_PREVIEW_PAD = 20

_TOO_LONG_TEXT = (
    "⚠️ <b>Слишком длинное сообщение</b>\n\n"
    "Максимум — 4000 символов. Сократи и пришли ещё раз."
)


def _pad_to_width(text: str, width: int = _PREVIEW_PAD) -> str:
    if len(text) < width:
        text += " " * (width - len(text)) + _ZWJ
    return text


def _question_preview(stored_text: str, is_photo: bool = False) -> str:
    line = _pad_to_width((stored_text or "…").replace("\n", " "))
    parts = ["✉️ <b>Твой вопрос сешке:</b>"]
    if is_photo:
        parts.append("📎 <i>Фото прикреплено</i>")
    parts.append(f"<pre>{html.escape(line)}</pre>")
    return "\n".join(parts)


def _ask_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✖️ Отмена", callback_data="q:cancel")],
    ])


def _ask_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📤 Отправить", callback_data="q:send"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data="q:discard"),
        ],
    ])


@router.callback_query(CatalogCb.filter(F.action == "ask"))
async def cb_ask(call: CallbackQuery, callback_data: CatalogCb, state: FSMContext) -> None:
    shop = await get_shop(callback_data.shop_id)
    if not shop:
        await call.answer("⚠️ Магазин не найден", show_alert=True)
        return
    if not await shop_regular_admins(shop.id):
        # Кнопка могла остаться на старой карточке: админа уже сняли.
        await call.answer("❓ У этой сешки пока нет админа", show_alert=True)
        return
    await upsert_user(call.from_user.id, call.from_user.username)
    new_id = await show_text_view(
        call,
        f"❓ <b>Вопрос сешке «{html.escape(shop.name)}»</b>\n"
        "\n"
        "Напиши свой вопрос текстом или фото — ответит админ сешки.\n"
        "\n"
        "<i>Отменить — /cancel</i>",
        _ask_cancel_kb(),
    )
    await state.update_data(
        shop_id=shop.id,
        src=callback_data.src,
        page=callback_data.page,
        flt=callback_data.flt,
        sort=callback_data.sort,
        q_msg_id=new_id,
    )
    await state.set_state(QuestionStates.waiting_text)
    await call.answer()


@router.callback_query(F.data == "q:cancel")
async def cb_ask_cancel(call: CallbackQuery, state: FSMContext) -> None:
    """«Отмена» на прашивании — возвращаем карточку сешки."""
    data = await state.get_data()
    await state.clear()
    user_id = await upsert_user(call.from_user.id, call.from_user.username)
    await redraw_shop_card(call, user_id, data)
    await call.answer()


@router.message(QuestionStates.waiting_text, Command("cancel"))
async def q_cancel(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    try:
        await message.delete()
    except TelegramBadRequest:
        pass
    if data.get("q_msg_id"):
        try:
            await message.bot.delete_message(message.chat.id, data["q_msg_id"])
        except TelegramBadRequest:
            pass
        await state.clear()
        await restore_card_fresh(message, data)
        return
    await state.clear()


@router.message(QuestionStates.waiting_text, F.photo | F.text)
async def q_save(message: Message, state: FSMContext, bot: Bot) -> None:
    user_id = await upsert_user(message.from_user.id, message.from_user.username)
    is_photo = bool(message.photo)
    text = ((message.caption if is_photo else message.text) or "").strip()
    data = await state.get_data()
    prompt_id = data.get("q_msg_id")

    if len(text) > 4000:
        if prompt_id is not None:
            await render(bot, message.chat.id, prompt_id, _TOO_LONG_TEXT)
        else:
            await message.answer(_TOO_LONG_TEXT)
        return

    stored_text = text or ("[Фото без подписи]" if is_photo else "")
    photo_file_id = message.photo[-1].file_id if is_photo else None
    try:
        await message.delete()
    except TelegramBadRequest:
        pass
    journal.remove(message.chat.id, message.message_id)

    await state.update_data(
        q_text=stored_text,
        q_is_photo=is_photo,
        q_photo_id=photo_file_id,
    )
    await state.set_state(QuestionStates.confirm)
    if prompt_id is not None:
        new_id = await render(
            bot, message.chat.id, prompt_id,
            _question_preview(stored_text, is_photo),
            _ask_confirm_kb(),
        )
    else:
        sent = await bot.send_message(
            message.chat.id,
            _question_preview(stored_text, is_photo),
            reply_markup=_ask_confirm_kb(),
        )
        journal.record(message.chat.id, sent.message_id)
        new_id = sent.message_id
    ws.set_active(user_id, new_id)


async def _notify_question_admins(
    bot: Bot, *, q_id: int, shop, user_tg_id: int, username: str | None,
    text: str, is_photo: bool, photo_id: str | None,
) -> None:
    """Вопрос — админам сешки в ЛС (с кнопками ответа) + в админ-чат
    супер-админу (только просмотр). Ошибки доставки некритичны."""
    try:
        from handlers.admin.questions import QCb
        uname = f"@{username}" if username else f"id={user_tg_id}"
        shop_name = html.escape(shop.name) if shop else "?"
        header = f"❓ <b>Вопрос сешке «{shop_name}»</b> #{q_id} от {html.escape(uname)}"
        body = f"{header}\n\n{html.escape(text)}" if text else header
        action_kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="↩️ Ответить",
                callback_data=QCb(action="reply", q_id=q_id, page=0).pack(),
            ),
            InlineKeyboardButton(
                text="✅ Закрыть",
                callback_data=QCb(action="close", q_id=q_id, page=0).pack(),
            ),
        ]])
        view_kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="👁 Открыть",
                callback_data=QCb(action="open", q_id=q_id, page=0).pack(),
            ),
        ]])

        async def _send(chat_id: int, kb: InlineKeyboardMarkup) -> None:
            if is_photo and photo_id:
                caption = body if text else header
                sent = await bot.send_photo(
                    chat_id, photo_id, caption=caption[:1024], reply_markup=kb,
                )
            else:
                sent = await bot.send_message(chat_id, body, reply_markup=kb)
            journal.record(chat_id, sent.message_id)

        for chat_id in [tg for tg in await shop_regular_admins(shop.id) if tg != user_tg_id]:
            try:
                await _send(chat_id, action_kb)
            except Exception:
                log.exception("failed to forward question to admin %s", chat_id)
        if settings.ADMIN_CHAT_ID:
            try:
                await _send(settings.ADMIN_CHAT_ID, view_kb)
            except Exception:
                log.exception("failed to forward question to admin chat")
    except Exception:
        log.exception("failed to notify question admins")


@router.callback_query(QuestionStates.confirm, F.data == "q:send")
async def q_send(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    text = data.get("q_text") or ""
    await state.clear()
    user_id = await upsert_user(call.from_user.id, call.from_user.username)
    shop = await get_shop(int(data["shop_id"])) if data.get("shop_id") else None
    q_id = await save_question(
        user_id, data["shop_id"], text, photo_id=data.get("q_photo_id"),
    )
    if shop:
        await _notify_question_admins(
            bot, q_id=q_id, shop=shop,
            user_tg_id=call.from_user.id, username=call.from_user.username,
            text=text, is_photo=bool(data.get("q_is_photo")), photo_id=data.get("q_photo_id"),
        )
    await redraw_shop_card(call, user_id, data)
    await call.answer("✅ Вопрос отправлен")


@router.callback_query(QuestionStates.confirm, F.data == "q:discard")
async def q_discard(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    user_id = await upsert_user(call.from_user.id, call.from_user.username)
    await redraw_shop_card(call, user_id, data)
    await call.answer("🗑 Удалено")
