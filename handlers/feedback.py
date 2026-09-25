import html
import logging
from datetime import date

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
from data.repos.feedback_repo import save_feedback
from data.repos.shops import get_shop
from data.repos.subs import is_subscribed, list_subscribed
from data.repos.users import is_admin, upsert_user
from keyboards.catalog_kb import CatalogCb, shop_card_kb
from keyboards.main_kb import main_menu
from services.catalog import FLT_ALL, SORT_NAME
from services.card_view import send_shop_card, show_shop_card, show_text_view
from services.chat_journal import journal
from services.chat_render import render, render_focus
from services.maps import yandex_maps_url
from services.texts import t
from services.workspace import ws
from states.feedback_states import FeedbackStates

log = logging.getLogger(__name__)
router = Router(name="feedback")

# Ширина бабла превью задаётся текстом, а кнопки растягиваются под бабл.
# Паддинг пробелами + zero-width joiner не даёт Telegram обрезать хвостовые
# пробелы и «разгоняет» превью до ширины двух кнопок «Отправить»/«Удалить».
_ZWJ = "\u200d"
_PREVIEW_PAD = 20

_TOO_LONG_TEXT = (
    "⚠️ <b>Слишком длинное сообщение</b>\n\n"
    "Максимум — 4000 символов. Сократи и пришли ещё раз."
)


def _pad_to_width(text: str, width: int = _PREVIEW_PAD) -> str:
    """Довести видимую длину строки до width символов (для превью)."""
    if len(text) < width:
        text += " " * (width - len(text)) + _ZWJ
    return text


def _report_preview(stored_text: str, is_photo: bool = False) -> str:
    """Превью репорта/сообщения с подписью о прикреплённом фото."""
    line = _pad_to_width((stored_text or "…").replace("\n", " "))
    parts = ["✉️ <b>Ваше сообщение:</b>"]
    if is_photo:
        parts.append("📎 <i>Фото прикреплено</i>")
    parts.append(f"<pre>{html.escape(line)}</pre>")
    return "\n".join(parts)


def _report_cancel_kb() -> InlineKeyboardMarkup:
    """Кнопка «Отмена» на экране-прашивании «Исправить неточность»: возвращает
    к карточке сешки (карточка превращена в прашивание, а не прислана снизу)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✖️ Отмена", callback_data="fb:report_cancel")],
    ])


def _report_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📤 Отправить", callback_data="fb:report_send"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data="fb:report_discard"),
        ],
    ])


def _fb_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📤 Отправить", callback_data="fb:send"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data="fb:discard"),
        ],
    ])


async def _back_to_menu(call: CallbackQuery) -> None:
    """Экран превращается в главное меню (после отмены/удаления фидбека)."""
    admin = await is_admin(call.from_user.id)
    new_id = await render(
        call.bot, call.message.chat.id, call.message.message_id,
        await t("start.welcome"),
        await main_menu(is_admin=admin),
    )
    ws.set_active(call.from_user.id, new_id)


def _fb_picker_kb(shops) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"🏪 {s.name}", callback_data=f"fb:shop:{s.id}")]
        for s in shops
    ]
    rows.append([InlineKeyboardButton(text="💬 Общее сообщение", callback_data="fb:noshop")])
    rows.append([InlineKeyboardButton(text="✖️ Отмена", callback_data="fb:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _ask_shop(message: Message, state: FSMContext, user_id: int) -> None:
    """/feedback: команда «фокусируется» на текущем экране (старое меню/помощь
    превращается в пикер магазинов, ввод команды удаляется)."""
    shops, _total = await list_subscribed(user_id, limit=10, offset=0)
    if not shops:
        text = (
            "✉️ <b>Сообщение администратору</b>\n"
            "\n"
            "Опиши, что хочешь сообщить — приму и передам.\n"
            "\n"
            "<i>Отменить — /cancel</i>"
        )
        new_id = await render_focus(message.bot, message, text)
        await state.update_data(fb_msg_id=new_id)
        await state.set_state(FeedbackStates.waiting_text)
        return

    text = (
        "✉️ <b>Сообщение администратору</b>\n"
        "\n"
        "К какому магазину относится сообщение?\n"
        "<i>Если ни к какому — выбери «Общее сообщение».</i>"
    )
    new_id = await render_focus(
        message.bot, message, text, _fb_picker_kb(shops),
    )
    await state.update_data(fb_msg_id=new_id)
    await state.set_state(FeedbackStates.pick_shop)


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
    text = (
        f"📝 <b>Сообщение про «{html.escape(shop.name)}»</b>\n"
        "\n"
        "Опиши, что хочешь сообщить — приму и передам.\n"
        "\n"
        "<i>Отменить — /cancel</i>"
    )
    new_id = await render(call.bot, call.message.chat.id, call.message.message_id, text)
    ws.set_active(call.from_user.id, new_id)
    await state.update_data(shop_id=shop.id, fb_msg_id=new_id)
    await state.set_state(FeedbackStates.waiting_text)
    await call.answer()


@router.callback_query(FeedbackStates.pick_shop, F.data == "fb:noshop")
async def cb_noshop(call: CallbackQuery, state: FSMContext) -> None:
    text = (
        "💬 <b>Общее сообщение</b>\n"
        "\n"
        "Опиши, что хочешь сообщить — приму и передам.\n"
        "\n"
        "<i>Отменить — /cancel</i>"
    )
    new_id = await render(call.bot, call.message.chat.id, call.message.message_id, text)
    ws.set_active(call.from_user.id, new_id)
    await state.update_data(shop_id=None, fb_msg_id=new_id)
    await state.set_state(FeedbackStates.waiting_text)
    await call.answer()


@router.callback_query(F.data == "fb:cancel")
async def cb_cancel(call: CallbackQuery, state: FSMContext) -> None:
    """«Отмена» на пикере магазинов: экран превращается в главное меню."""
    await state.clear()
    await _back_to_menu(call)
    await call.answer()


async def _redraw_shop_card(call: CallbackQuery, user_id: int, data: dict) -> None:
    """Вернуть сообщение к карточке сешки («переброс» из репорт-флоу)."""
    shop_id = data.get("shop_id")
    if not shop_id:
        try:
            await call.message.delete()
        except TelegramBadRequest:
            pass
        return
    shop = await get_shop(int(shop_id))
    if not shop:
        return
    tracked = await is_subscribed(user_id, shop.id)
    has_prices = bool(shop.price_start and shop.price_step is not None)
    kb = shop_card_kb(
        shop.id, tracked,
        src=data.get("src", "cat"),
        page=data.get("page", 0),
        flt=data.get("flt", FLT_ALL),
        sort=data.get("sort", SORT_NAME),
        has_prices=has_prices,
        maps_url=yandex_maps_url(shop.address),
    )
    card_id = await show_shop_card(call, shop, date.today(), is_tracked=tracked, kb=kb)
    ws.open_card(user_id, card_id)


async def _restore_card_fresh(message: Message, data: dict) -> None:
    """Cancel-фолбэк без колбэка: удалить прашивание и отправить карточку заново."""
    shop_id = data.get("shop_id")
    if not shop_id:
        return
    shop = await get_shop(int(shop_id))
    if not shop:
        return
    user_id = await upsert_user(message.from_user.id, message.from_user.username)
    tracked = await is_subscribed(user_id, shop.id)
    has_prices = bool(shop.price_start and shop.price_step is not None)
    kb = shop_card_kb(
        shop.id, tracked,
        src=data.get("src", "cat"),
        page=data.get("page", 0),
        flt=data.get("flt", FLT_ALL),
        sort=data.get("sort", SORT_NAME),
        has_prices=has_prices,
        maps_url=yandex_maps_url(shop.address),
    )
    card_id = await send_shop_card(message.bot, message.chat.id, shop, date.today(), is_tracked=tracked, kb=kb)
    ws.open_card(user_id, card_id)


@router.callback_query(CatalogCb.filter(F.action == "report"))
async def cb_report_shop(call: CallbackQuery, callback_data: CatalogCb, state: FSMContext) -> None:
    shop = await get_shop(callback_data.shop_id)
    if not shop:
        await call.answer("⚠️ Магазин не найден", show_alert=True)
        return
    await upsert_user(call.from_user.id, call.from_user.username)
    # Карточка на том же сообщении превращается в прашивание.
    new_id = await show_text_view(
        call,
        f"⚒️ <b>Сообщить о неточности — «{html.escape(shop.name)}»</b>\n"
        "\n"
        "Опиши, что не так — текстом или фото (можно с подписью).\n"
        "\n"
        "<i>Отменить — /cancel</i>",
        _report_cancel_kb(),
    )
    await state.update_data(
        shop_id=shop.id,
        src=callback_data.src,
        page=callback_data.page,
        flt=callback_data.flt,
        sort=callback_data.sort,
        report_msg_id=new_id,
    )
    await state.set_state(FeedbackStates.waiting_text)
    await call.answer()


@router.callback_query(F.data == "fb:report_cancel")
async def cb_report_cancel(call: CallbackQuery, state: FSMContext) -> None:
    """«Отмена» на прашивании — возвращаем карточку сешки."""
    data = await state.get_data()
    await state.clear()
    user_id = await upsert_user(call.from_user.id, call.from_user.username)
    await _redraw_shop_card(call, user_id, data)
    await call.answer()


@router.message(FeedbackStates.waiting_text, Command("cancel"))
async def fb_cancel(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    # Сам /cancel удаляется всегда — не оставляем командный мусор.
    try:
        await message.delete()
    except TelegramBadRequest:
        pass
    if data.get("report_msg_id"):
        # Репорт-флоу: прашивание удаляется, карточка возвращается.
        try:
            await message.bot.delete_message(message.chat.id, data["report_msg_id"])
        except TelegramBadRequest:
            pass
        await state.clear()
        await _restore_card_fresh(message, data)
        return
    # Обычный флоу (/feedback): экран-прашивание просто исчезает.
    await state.clear()
    if data.get("fb_msg_id") is not None:
        try:
            await message.bot.delete_message(message.chat.id, data["fb_msg_id"])
        except TelegramBadRequest:
            pass


@router.message(FeedbackStates.waiting_text, F.photo | F.text)
async def fb_save(message: Message, state: FSMContext, bot: Bot) -> None:
    user_id = await upsert_user(message.from_user.id, message.from_user.username)
    is_photo = bool(message.photo)
    text = (message.caption if is_photo else message.text) or ""
    text = text.strip()
    data = await state.get_data()

    target_prompt = data.get("report_msg_id") or data.get("fb_msg_id")
    if len(text) > 4000:
        # Ввод не удаляем (это черновик, который нужно сократить), экран
        # прашивания превращается в ошибку.
        if target_prompt is not None:
            await render(bot, message.chat.id, target_prompt, _TOO_LONG_TEXT)
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

    if data.get("report_msg_id") is not None:
        # Флоу «Исправить неточность»: прашивание превращается в превью.
        await state.update_data(
            report_text=stored_text,
            report_is_photo=is_photo,
            report_photo_id=photo_file_id,
        )
        await state.set_state(FeedbackStates.confirm)
        new_id = await render(
            bot, message.chat.id, data["report_msg_id"],
            _report_preview(stored_text, is_photo),
            _report_confirm_kb(),
        )
        ws.set_active(user_id, new_id)
        return

    # Обычный флоу (/feedback): прашивание превращается в превью.
    await state.update_data(
        fb_text=stored_text,
        fb_is_photo=is_photo,
        fb_photo_id=photo_file_id,
    )
    await state.set_state(FeedbackStates.confirm)
    if data.get("fb_msg_id") is not None:
        new_id = await render(
            bot, message.chat.id, data["fb_msg_id"],
            _report_preview(stored_text, is_photo),
            _fb_confirm_kb(),
        )
    else:
        sent = await bot.send_message(
            message.chat.id,
            _report_preview(stored_text, is_photo),
            reply_markup=_fb_confirm_kb(),
        )
        new_id = sent.message_id
    ws.set_active(user_id, new_id)


async def _notify_admin(
    bot: Bot, *, user_id: int, username: str | None, shop_id: int | None,
    fb_id: int, text: str, is_photo: bool, photo_id: str | None,
) -> None:
    if not settings.ADMIN_CHAT_ID:
        return
    try:
        uname = f"@{username}" if username else f"id={user_id}"
        shop_label = ""
        if shop_id:
            shop = await get_shop(shop_id)
            if shop:
                shop_label = f"\n🛍 Магазин: <b>{html.escape(shop.name)}</b>"
        header = f"📨 <b>Feedback #{fb_id}</b> от {html.escape(uname)}:{shop_label}"
        if is_photo and photo_id:
            caption = f"{header}\n\n{html.escape(text)}" if text else header
            await bot.send_photo(
                settings.ADMIN_CHAT_ID, photo_id,
                caption=caption[:1024],
            )
        else:
            await bot.send_message(
                settings.ADMIN_CHAT_ID,
                f"{header}\n\n{html.escape(text)}",
            )
    except Exception:
        log.exception("failed to forward feedback to admin chat")


@router.callback_query(FeedbackStates.confirm, F.data == "fb:send")
async def cb_send(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    text = data.get("fb_text") or ""
    await state.clear()
    user_id = await upsert_user(call.from_user.id, call.from_user.username)
    fb_id = await save_feedback(user_id, text, shop_id=data.get("shop_id"))
    await _notify_admin(
        bot,
        user_id=user_id, username=call.from_user.username,
        shop_id=data.get("shop_id"), fb_id=fb_id, text=text,
        is_photo=bool(data.get("fb_is_photo")), photo_id=data.get("fb_photo_id"),
    )
    await show_text_view(
        call,
        "✅ <b>Спасибо!</b>\n"
        "Сообщение получено — администратор увидит его в ближайшее время.",
        None,
    )
    await call.answer("✅ Отправлено")


@router.callback_query(FeedbackStates.confirm, F.data == "fb:discard")
async def cb_discard(call: CallbackQuery, state: FSMContext) -> None:
    """«Удалить» на превью: черновик удаляется, вместо превью — главное меню."""
    await state.clear()
    await _back_to_menu(call)
    await call.answer("🗑 Удалено")


@router.callback_query(FeedbackStates.confirm, F.data == "fb:report_send")
async def cb_report_send(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    text = data.get("report_text") or ""
    await state.clear()
    user_id = await upsert_user(call.from_user.id, call.from_user.username)
    fb_id = await save_feedback(user_id, text, shop_id=data.get("shop_id"))
    await _notify_admin(
        bot,
        user_id=user_id, username=call.from_user.username,
        shop_id=data.get("shop_id"), fb_id=fb_id, text=text,
        is_photo=bool(data.get("report_is_photo")), photo_id=data.get("report_photo_id"),
    )
    await _redraw_shop_card(call, user_id, data)
    await call.answer("✅ Отправлено")


@router.callback_query(FeedbackStates.confirm, F.data == "fb:report_discard")
async def cb_report_discard(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    user_id = await upsert_user(call.from_user.id, call.from_user.username)
    await _redraw_shop_card(call, user_id, data)
    await call.answer("🗑 Удалено")


@router.message(Command("cancel"))
async def cmd_cancel_anywhere(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        # Сам /cancel превращается в инфо-строку, без нового бабла.
        await render(message.bot, message.chat.id, message.message_id, "ℹ️ Нечего отменять.")
        return
    await state.clear()
    try:
        await message.delete()
    except TelegramBadRequest:
        pass