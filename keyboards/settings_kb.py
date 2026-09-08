from datetime import date, time

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

TIME_PRESETS: list[time] = [time(8), time(9), time(12), time(18), time(20)]
PAUSE_OPTIONS: list[int] = [1, 3, 7, 14]
WEEKDAY_NAMES_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
LEAD_DAYS_OPTIONS: list[int] = [0, 1, 2, 3, 5, 7]
EVENT_LABELS_RU = {"arrival": "🚚 Завоз", "max_discount": "💰 Дешёвые цены"}


def _day_word(n: int) -> str:
    last_two = n % 100
    last = n % 10
    if 11 <= last_two <= 14:
        return "дней"
    if last == 1:
        return "день"
    if 2 <= last <= 4:
        return "дня"
    return "дней"


def lead_days_label(n: int) -> str:
    return "В день события" if n == 0 else f"За {n} {_day_word(n)}"


class SettingsCb(CallbackData, prefix="set"):
    action: str  # "open" | "time" | "pause" | "back"


class TimeCb(CallbackData, prefix="stm"):
    hour: int
    minute: int = 0


class PauseCb(CallbackData, prefix="spz"):
    days: int  # 0 = отмена паузы


class ShopNotifCb(CallbackData, prefix="snc"):
    shop_id: int
    action: str  # "open" | "tog" | "twd" | "back" |
                 # "evopen" | "evlead" | "evtime" | "evoff" (event wizard)
    value: str = ""    # имя флага / номер дня недели / "event[|lead[|HH:MM]]"
    src: str = "my"
    page: int = 0


def settings_menu_kb(notify_time: time, pause_until: date | None) -> InlineKeyboardMarkup:
    pause_text = (
        f"⏸ Пауза до {pause_until.strftime('%d.%m')}"
        if pause_until and pause_until > date.today()
        else "⏸ Поставить на паузу"
    )
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"🕐 Время уведомлений: {notify_time.strftime('%H:%M')}",
            callback_data=SettingsCb(action="time").pack(),
        )],
        [InlineKeyboardButton(text=pause_text, callback_data=SettingsCb(action="pause").pack())],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="help:open",)],
        [InlineKeyboardButton(text="🏠 В меню", callback_data="menu:open")],
    ])


def time_picker_kb(current: time) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for t in TIME_PRESETS:
        label = t.strftime("%H:%M")
        if t.hour == current.hour and t.minute == current.minute:
            label = f"• {label} •"
        row.append(InlineKeyboardButton(
            text=label,
            callback_data=TimeCb(hour=t.hour, minute=t.minute).pack(),
        ))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data=SettingsCb(action="open").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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


def _event_summary(setting) -> str:
    if not setting.enabled:
        return "выключено"
    eff = setting.notify_time
    when = lead_days_label(setting.lead_days).lower()
    return f"{when}, {eff.strftime('%H:%M')}" if eff else f"{when}, время как в настройках"


def shop_notif_cycle_kb(
    shop_id: int,
    flags: dict[str, bool],
    event_settings: dict,
    src: str,
    page: int,
) -> InlineKeyboardMarkup:
    def mark(v: bool) -> str:
        return "✅" if v else "⬜"

    rows = [
        [InlineKeyboardButton(
            text=f"{EVENT_LABELS_RU['arrival']}: {_event_summary(event_settings['arrival'])}",
            callback_data=ShopNotifCb(
                shop_id=shop_id, action="evopen", value="arrival", src=src, page=page,
            ).pack(),
        )],
        [InlineKeyboardButton(
            text=f"{EVENT_LABELS_RU['max_discount']}: {_event_summary(event_settings['max_discount'])}",
            callback_data=ShopNotifCb(
                shop_id=shop_id, action="evopen", value="max_discount", src=src, page=page,
            ).pack(),
        )],
        [InlineKeyboardButton(
            text=f"{mark(flags['notify_middle'])} ⚖️ Середина цикла",
            callback_data=ShopNotifCb(
                shop_id=shop_id, action="tog", value="notify_middle", src=src, page=page,
            ).pack(),
        )],
        [InlineKeyboardButton(
            text="◀️ К магазину",
            callback_data=ShopNotifCb(
                shop_id=shop_id, action="back", src=src, page=page,
            ).pack(),
        )],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def event_lead_picker_kb(shop_id: int, event: str, src: str, page: int) -> InlineKeyboardMarkup:
    """Step 1 of the wizard: how many days before the event to warn."""
    rows = [
        [InlineKeyboardButton(
            text=lead_days_label(n),
            callback_data=ShopNotifCb(
                shop_id=shop_id, action="evlead", value=f"{event}|{n}", src=src, page=page,
            ).pack(),
        )]
        for n in LEAD_DAYS_OPTIONS
    ]
    rows.append([InlineKeyboardButton(
        text="🚫 Отключить уведомления",
        callback_data=ShopNotifCb(
            shop_id=shop_id, action="evoff", value=event, src=src, page=page,
        ).pack(),
    )])
    rows.append([InlineKeyboardButton(
        text="◀️ Назад",
        callback_data=ShopNotifCb(shop_id=shop_id, action="open", src=src, page=page).pack(),
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def event_time_picker_kb(shop_id: int, event: str, lead: int, src: str, page: int) -> InlineKeyboardMarkup:
    """Step 2 of the wizard: what time of day to send it."""
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for t in TIME_PRESETS:
        hhmm = t.strftime("%H:%M")
        row.append(InlineKeyboardButton(
            text=hhmm,
            callback_data=ShopNotifCb(
                shop_id=shop_id, action="evtime", value=f"{event}|{lead}|{hhmm}", src=src, page=page,
            ).pack(),
        ))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(
        text="🕐 Как в общих настройках",
        callback_data=ShopNotifCb(
            shop_id=shop_id, action="evtime", value=f"{event}|{lead}|", src=src, page=page,
        ).pack(),
    )])
    rows.append([InlineKeyboardButton(
        text="◀️ Назад",
        callback_data=ShopNotifCb(
            shop_id=shop_id, action="evopen", value=event, src=src, page=page,
        ).pack(),
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def shop_notif_weekdays_kb(
    shop_id: int,
    selected: set[int],
    src: str,
    page: int,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for i, name in enumerate(WEEKDAY_NAMES_RU):
        text = f"✅ {name}" if i in selected else name
        row.append(InlineKeyboardButton(
            text=text,
            callback_data=ShopNotifCb(
                shop_id=shop_id, action="twd", value=str(i), src=src, page=page,
            ).pack(),
        ))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(
        text="◀️ К магазину",
        callback_data=ShopNotifCb(
            shop_id=shop_id, action="back", src=src, page=page,
        ).pack(),
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)
