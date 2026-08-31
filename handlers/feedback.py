import html
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import settings
from data.repos.feedback_repo import save_feedback
from data.repos.shops import get_shop
from data.repos.subs import list_subscribed
from data.repos.users import upsert_user
from keyboards.catalog_kb import CatalogCb
from states.feedback_states import FeedbackStates

log = logging.getLogger(__name__)
router = Router(name="feedback")


def _no_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Общее сообщение", callback_data="fb:noshop")],
        [InlineKeyboardButton(text="✖️ Отмена", callback_data="fb:cancel")],
    ])


async def _ask_shop(message: Message, state: FSMContext, user_id: int) -> None:
    shops, total = await list_subscribed(user_id, limit=10, offset=0)
    if not shops:
        await state.set_state(FeedbackStates.waiting_text)
        await message.answer(
            "✉️ <b>Сообщение администратору</b>\n"
            "\n"
            "Опиши, что хочешь сообщить — приму и передам.\n"
            "\n"
            "<i>Отменить — /cancel</i>"
        )
        return

    rows = [
        [InlineKeyboardButton(text=f"🏪 {s.name}", callback_data=f"fb:shop:{s.id}")]
        for s in shops
    ]
    rows.append([InlineKeyboardButton(text="💬 Общее сообщение", callback_data="fb:noshop")])
    rows.append([InlineKeyboardButton(text="✖️ Отмена", callback_data="fb:cancel")])
    await state.set_state(FeedbackStates.pick_shop)
    await message.answer(
        "✉️ <b>Сообщение администратору</b>\n"
        "\n"
        "К какому магазину относится сообщение?\n"
        "<i>Если ни к какому — выбери «Общее сообщение».</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.message(Command("feedback"))
async def cmd_feedback(message: Message, state: FSMContext) -> None:
    await state.clear()
    user_id = await upsert_user(message.from_user.id, message.from_user.username)
    await _ask_shop(message, state, user_id)


@router.callback_query(FeedbackStates.pick_shop, F.data.startswith("fb:shop:"))
async def cb_pick_shop(call: CallbackQuery, state: FSMContext) -> None:
    try:
        shop_id = int(call.data.rsplit(":", 1)[-1])
    except ValueError:
        await call.answer("⚠️ Некорректный ввод", show_alert=True)
        return
    shop = await get_shop(shop_id)
    if not shop:
        await call.answer("⚠️ Магазин не найден", show_alert=True)
        return
    await state.update_data(shop_id=shop_id)
    await state.set_state(FeedbackStates.waiting_text)
    await call.message.answer(
        f"📝 <b>Сообщение про «{html.escape(shop.name)}»</b>\n"
        "\n"
        "Опиши, что хочешь сообщить — приму и передам.\n"
        "\n"
        "<i>Отменить — /cancel</i>"
    )
    await call.answer()


@router.callback_query(FeedbackStates.pick_shop, F.data == "fb:noshop")
async def cb_noshop(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(shop_id=None)
    await state.set_state(FeedbackStates.waiting_text)
    await call.message.answer(
        "💬 <b>Общее сообщение</b>\n"
        "\n"
        "Опиши, что хочешь сообщить — приму и передам.\n"
        "\n"
        "<i>Отменить — /cancel</i>"
    )
    await call.answer()


@router.callback_query(CatalogCb.filter(F.action == "report"))
async def cb_report_shop(call: CallbackQuery, callback_data: CatalogCb, state: FSMContext) -> None:
    shop = await get_shop(callback_data.shop_id)
    if not shop:
        await call.answer("⚠️ Магазин не найден", show_alert=True)
        return
    await upsert_user(call.from_user.id, call.from_user.username)
    await state.update_data(shop_id=shop.id)
    await state.set_state(FeedbackStates.waiting_text)
    await call.message.answer(
        f"⚒️ <b>Сообщить о неточности — «{html.escape(shop.name)}»</b>\n"
        "\n"
        "Опиши, что не так — текстом или фото (можно с подписью).\n"
        "\n"
        "<i>Отменить — /cancel</i>"
    )
    await call.answer()


@router.callback_query(F.data == "fb:cancel")
async def cb_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.answer("✖️ Отменил.")
    await call.answer()


@router.message(FeedbackStates.waiting_text, Command("cancel"))
async def fb_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("✖️ Отменил.")


@router.message(FeedbackStates.waiting_text, F.photo | F.text)
async def fb_save(message: Message, state: FSMContext, bot: Bot) -> None:
    user_id = await upsert_user(message.from_user.id, message.from_user.username)
    is_photo = bool(message.photo)
    text = (message.caption if is_photo else message.text) or ""
    text = text.strip()
    if len(text) > 4000:
        await message.answer(
            "⚠️ <b>Слишком длинное сообщение</b>\n\n"
            "Максимум — 4000 символов. Сократи и пришли ещё раз."
        )
        return
    stored_text = text or ("[Фото без подписи]" if is_photo else "")
    data = await state.get_data()
    shop_id = data.get("shop_id")
    fb_id = await save_feedback(user_id, stored_text, shop_id=shop_id)
    await state.clear()
    await message.answer(
        "✅ <b>Спасибо!</b>\n"
        "Сообщение получено — администратор увидит его в ближайшее время."
    )

    if settings.ADMIN_CHAT_ID:
        try:
            uname = f"@{message.from_user.username}" if message.from_user.username else f"id={message.from_user.id}"
            shop_label = ""
            if shop_id:
                shop = await get_shop(shop_id)
                if shop:
                    shop_label = f"\n🛍 Магазин: <b>{html.escape(shop.name)}</b>"
            header = f"📨 <b>Feedback #{fb_id}</b> от {html.escape(uname)}:{shop_label}"
            if is_photo:
                caption = f"{header}\n\n{html.escape(text)}" if text else header
                await bot.send_photo(
                    settings.ADMIN_CHAT_ID,
                    message.photo[-1].file_id,
                    caption=caption[:1024],
                )
            else:
                await bot.send_message(
                    settings.ADMIN_CHAT_ID,
                    f"{header}\n\n{html.escape(text)}",
                )
        except Exception:
            log.exception("failed to forward feedback to admin chat")


@router.message(Command("cancel"))
async def cmd_cancel_anywhere(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        await message.answer("ℹ️ Нечего отменять.")
        return
    await state.clear()
    await message.answer("✖️ Отменил.")
