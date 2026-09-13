from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from services.chat_render import render


async def safe_edit(
    call: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> int:
    """Teleport-aware перерисовка меню. Возвращает актуальный message_id."""
    return await render(
        call.bot, call.message.chat.id, call.message.message_id, text, reply_markup,
    )


# Пустая клавиатура: убирает inline-кнопки при превращении экрана.
EMPTY_KB = InlineKeyboardMarkup(inline_keyboard=[])


async def render_screen_msg(
    message: Message,
    state: FSMContext,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    *,
    keep_input: bool = False,
) -> int:
    """Трансформ текущего экрана (wizard_msg_id) после текстового ввода.

    Ввод администратора удаляется (кроме keep_input=True — ошибки с черновиком),
    экран перерисовывается на месте. Возвращает актуальный message_id.
    """
    data = await state.get_data()
    msg_id = data.get("wizard_msg_id")
    if not keep_input:
        try:
            await message.delete()
        except TelegramBadRequest:
            pass
    target = msg_id if msg_id is not None else message.message_id
    new_id = await render(message.bot, message.chat.id, target, text, reply_markup)
    await state.update_data(wizard_msg_id=new_id)
    return new_id


async def render_screen_call(
    call: CallbackQuery,
    state: FSMContext,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> int:
    """Трансформ текущего экрана (wizard_msg_id) после нажатия кнопки."""
    data = await state.get_data()
    msg_id = data.get("wizard_msg_id")
    target = msg_id if msg_id is not None else call.message.message_id
    new_id = await render(call.bot, call.message.chat.id, target, text, reply_markup)
    await state.update_data(wizard_msg_id=new_id)
    return new_id