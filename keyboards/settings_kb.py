from datetime import date

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

PAUSE_OPTIONS: list[int] = [1, 3, 7, 14]


class SettingsCb(CallbackData, prefix="set"):
    action: str  # "open" | "pause" | "toggle_arrival" | "toggle_cheap"


class PauseCb(CallbackData, prefix="spz"):
    days: int  # 0 = отмена паузы


def _mark(v: bool) -> str:
    return "✅" if v else "⬜"


def settings_menu_kb(
    pause_until: date | None, notify_arrival: bool, notify_cheap_day: bool,
) -> InlineKeyboardMarkup:
    pause_text = (
        f"⏸ Пауза до {pause_until.strftime('%d.%m')}"
        if pause_until and pause_until > date.today()
        else "⏸ Поставить на паузу"
    )
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{_mark(notify_arrival)} 🚚 Уведомлять о завозе (за день, в 9:00)",
            callback_data=SettingsCb(action="toggle_arrival").pack(),
        )],
        [InlineKeyboardButton(
            text=f"{_mark(notify_cheap_day)} 💰 Уведомлять о дешёвом дне (в 9:00)",
            callback_data=SettingsCb(action="toggle_cheap").pack(),
        )],
        [InlineKeyboardButton(text=pause_text, callback_data=SettingsCb(action="pause").pack())],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="help:open",)],
        [InlineKeyboardButton(text="🏠 В меню", callback_data="menu:open")],
    ])


def pause_picker_kb(active: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for days in PAUSE_OPTIONS:
        row.append(InlineKeyboardButton(
            text=f"{days} дн.",
            callback_data=PauseCb(days=days).pack(),
        ))
    rows.append(row)
    if active:
        rows.append([InlineKeyboardButton(
            text="✖️ Снять паузу",
            callback_data=PauseCb(days=0).pack(),
        )])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data=SettingsCb(action="open").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)
