from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


class AdminCb(CallbackData, prefix="adm"):
    action: str
    value: str | None = None


CHAIN_OPTIONS = ["Megahand", "Favorite"]


def chain_picker_kb() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=c, callback_data=AdminCb(action="chain", value=c).pack())]
            for c in CHAIN_OPTIONS]
    rows.append([InlineKeyboardButton(
        text="Независимый (без сети)",
        callback_data=AdminCb(action="chain", value="").pack(),
    )])
    rows.append([InlineKeyboardButton(text="✖️ Отмена", callback_data=AdminCb(action="cancel").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cycle_picker_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="14 дней", callback_data=AdminCb(action="cycle", value="14").pack()),
            InlineKeyboardButton(text="21 день", callback_data=AdminCb(action="cycle", value="21").pack()),
        ],
        [InlineKeyboardButton(text="Без цикла", callback_data=AdminCb(action="cycle", value="0").pack())],
        [InlineKeyboardButton(text="✖️ Отмена", callback_data=AdminCb(action="cancel").pack())],
    ])


def anchor_picker_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Сегодня — день завоза", callback_data=AdminCb(action="anchor_today").pack())],
        [InlineKeyboardButton(text="Ввести дату вручную", callback_data=AdminCb(action="anchor_manual").pack())],
        [InlineKeyboardButton(text="Пропустить", callback_data=AdminCb(action="anchor_skip").pack())],
        [InlineKeyboardButton(text="✖️ Отмена", callback_data=AdminCb(action="cancel").pack())],
    ])


def skip_or_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Пропустить", callback_data=AdminCb(action="skip").pack())],
        [InlineKeyboardButton(text="✖️ Отмена", callback_data=AdminCb(action="cancel").pack())],
    ])


def photos_done_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Готово / дальше", callback_data=AdminCb(action="photos_done").pack())],
        [InlineKeyboardButton(text="Без фото", callback_data=AdminCb(action="photos_skip").pack())],
        [InlineKeyboardButton(text="✖️ Отмена", callback_data=AdminCb(action="cancel").pack())],
    ])


def confirm_kb(action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Создать", callback_data=AdminCb(action=f"{action}_yes").pack())],
        [InlineKeyboardButton(text="✏️ Изменить поле", callback_data=AdminCb(action="edit_menu").pack())],
        [
            InlineKeyboardButton(text="🔄 Начать заново", callback_data=AdminCb(action="restart").pack()),
            InlineKeyboardButton(text="✖️ Отмена", callback_data=AdminCb(action="cancel").pack()),
        ],
    ])


def edit_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛍 Имя", callback_data=AdminCb(action="edit", value="name").pack()),
         InlineKeyboardButton(text="📍 Адрес", callback_data=AdminCb(action="edit", value="address").pack())],
        [InlineKeyboardButton(text="🏷 Сеть", callback_data=AdminCb(action="edit", value="chain").pack()),
         InlineKeyboardButton(text="🗓 Цикл", callback_data=AdminCb(action="edit", value="cycle").pack())],
        [InlineKeyboardButton(text="📅 Дата завоза", callback_data=AdminCb(action="edit", value="anchor").pack())],
        [InlineKeyboardButton(text="💰 Цена/кг", callback_data=AdminCb(action="edit", value="price_start").pack()),
         InlineKeyboardButton(text="📉 Шаг ₽/день", callback_data=AdminCb(action="edit", value="price_step").pack())],
        [InlineKeyboardButton(text="🕒 Время работы", callback_data=AdminCb(action="edit", value="working_hours").pack())],
        [InlineKeyboardButton(text="📝 Описание", callback_data=AdminCb(action="edit", value="description").pack())],
        [InlineKeyboardButton(text="📷 Фото (перезалить)", callback_data=AdminCb(action="edit", value="photos").pack())],
        [InlineKeyboardButton(text="👥 Админы", callback_data=AdminCb(action="edit", value="assign").pack())],
        [InlineKeyboardButton(text="← К превью", callback_data=AdminCb(action="back_to_confirm").pack())],
    ])


def dup_warning_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Всё равно создать", callback_data=AdminCb(action="dup_force").pack())],
        [InlineKeyboardButton(text="✏️ Изменить имя/адрес", callback_data=AdminCb(action="edit_menu").pack())],
        [InlineKeyboardButton(text="✖️ Отмена", callback_data=AdminCb(action="cancel").pack())],
    ])
