"""Editable user-facing texts (level 2 scope).

Texts are stored in `bot_texts` (key, value) with defaults in this module.
Format: HTML strings with optional `{var}` placeholders.

Usage:
    from services.texts import t
    welcome = await t("start.welcome", username="Andrew")
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

from data.db import pool

log = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, str]] = {}
_TTL = 60.0
_VAR_RE = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
_MAX_LEN = 4000


# ---------- defaults ----------

# Each key has: default value (HTML), allowed placeholders, human-readable description.
# Keys are grouped by section for the super-admin UI.

DEFAULTS: dict[str, str] = {
    # menu buttons
    "menu.btn.catalog":  "🛍 Каталог",
    "menu.btn.my_shops": "💘 Ну мои",
    "menu.btn.settings": "⚙️ Настройки",
    "menu.btn.help":     "❓ Помощь",
    "menu.btn.admin":    "🛠 Админка",

    # start / help
    "start.welcome": (
        "🛒 <b>Секонд-Бот Севастополя</b>\n"
        "<i>Завозы в секондах</i>\n"
        "\n"
        "Подскажу, когда выгоднее всего залететь: <b>завоз</b>, "
        "<b>среднячек</b> и <b>минимальная цена</b>.\n"
        "\n"
        "<blockquote>🛍 <b>Каталог</b> — все сешки\n"
        "💘 <b>Ну мои</b> — избранное\n"
        "⚙️ <b>Настройки</b> — расписание уведомлений</blockquote>\n"
        "\n"
        "Выбирай раздел 👇"
    ),
    "help.text": (
        "ℹ️ <b>Как пользоваться ботом</b>\n"
        "<b>Кто-то это читает ваще?🤠</b>\n"
        "\n"
        "<b>1. Найди магазин</b>\n"
        "Открой 🛍 <b>Каталог</b> и выбери интересный секонд-хенд.\n"
        "\n"
        "<b>2. Отметь как избранный</b>\n"
        "В карточке магазина нажми 💘 <b>Отслеживать</b>.\n"
        "\n"
        "<b>3. Получай напоминания</b>\n"
        "Я приду в выбранное время и напомню о завозе, "
        "середине цикла и максимальной скидке.\n"
        "\n"
        "<blockquote><b>Команды</b>\n"
        "/start — главное меню\n"
        "/help — эта справка\n"
        "/feedback — написать админу\n"
        "/cancel — отменить действие</blockquote>"
    ),

    # catalog
    "catalog.title":          "🛍 <b>Каталог секонд-хендов</b>",
    "catalog.found":          "Найдено: <b>{count}</b>",
    "catalog.hint":           "<i>Нажми на магазин, чтобы открыть карточку</i>",
    "catalog.legend": (
        "<blockquote>🔴 - завоз <b>недавно</b> (Цена - ⬆️ Вещи - 👍🏿)\n"
        "🟡 - <b>середина</b>\n"
        "🟢 - завоз <b>давно</b> (Цена - ⬇️ Вещи - 👎🏿)\n"
        "\n"
        "🚚 - <b>сегодня</b> день завоза\n"
        "📌 - вещи по <b>фиксированной</b> цене\n"
        "💘 - добавлен в <b>'Ну мои'</b> (Будут приходить уведомления перед завозами)</blockquote>"
    ),
    "catalog.empty": (
        "🛍 <b>Каталог пуст</b>\n"
        "\n"
        "Магазины пока не добавлены. Загляни позже."
    ),
    "catalog.empty_filter": (
        "🛍 <b>Ничего не найдено</b>\n"
        "\n"
        "Под текущий фильтр и поиск нет ни одного магазина."
    ),
    "catalog.shop_not_found": "Магазин не найден",
    "catalog.more_title": (
        "🛍 <b>Дополнительные фильтры</b>\n"
        "\n"
        "Выбери, что показать в каталоге."
    ),
    "catalog.sort_title": (
        "↕ <b>Сортировка</b>\n"
        "\n"
        "Как упорядочить магазины в списке?"
    ),
    "catalog.search.prompt": (
        "🔍 <b>Поиск магазинов</b>\n"
        "\n"
        "Пришли текст — найду по названию, адресу или сети.\n"
        "<i>Отменить — /cancel</i>"
    ),

    # my shops
    "myshops.title":           "💘 <b>Ну мои</b>",
    "myshops.empty": (
        "💘 <b>Ну мои</b>\n"
        "\n"
        "Здесь появятся магазины, по которым ты получаешь напоминания.\n"
        "\n"
        "<blockquote>1. Открой 🛍 <b>Каталог</b>\n"
        "2. Выбери магазин\n"
        "3. Нажми 💘 <b>Отслеживать</b></blockquote>"
    ),
    "myshops.list_header": (
        "💘 <b>Ну мои</b> · <b>{count}</b>\n"
        "\n"
        "<i>Нажми на магазин, чтобы открыть карточку</i>"
    ),
    "myshops.added":           "💘 Добавлено в 'Ну мои'",
    "myshops.removed":         "🗑 Убрано из моих",
    "myshops.unsub_all_ask": (
        "🗑 <b>Отписаться от всех магазинов?</b>\n"
        "\n"
        "В моих сейчас: <b>{count}</b>\n"
        "После подтверждения уведомления перестанут приходить."
    ),
    "myshops.unsub_all_done":  "🗑 Отписан от {count} магазинов",
    "myshops.sub_all_done":    "💘 Добавлено: {count}",
    "myshops.sub_all_already": "👌 Уже подписан на все магазины",

    # settings
    "settings.title":              "⚙️ <b>Настройки</b>",
    "settings.notify_time":        "🕐 Время уведомлений · <b>{time}</b>",
    "settings.pause_active":       "⏸ На паузе до <b>{date}</b>",
    "settings.pause_off":          "🔔 Уведомления активны",
    "settings.time_picker": (
        "🕐 <b>Время уведомлений</b>\n"
        "\n"
        "Когда присылать напоминания?"
    ),
    "settings.time_saved":         "🕐 Сохранено: {time}",
    "settings.pause_picker": (
        "⏸ <b>Пауза уведомлений</b>\n"
        "\n"
        "На сколько дней поставить на паузу?"
    ),
    "settings.pause_off_done":     "▶️ Пауза снята",
    "settings.pause_set_done":     "⏸ Пауза до {date}",
    "settings.shop_notif_title":   "⚙️ <b>Уведомления · «{shop_name}»</b>",
    "settings.shop_notif_cycle":   "<i>Отметь события, о которых напоминать</i>",
    "settings.shop_notif_wdays": (
        "<i>У магазина нет цикла — выбери дни недели для напоминаний</i>"
    ),
    "settings.need_track":         "Сначала добавь магазин в 💘 Ну мои",
    "settings.toggle_on":          "✅ Включено",
    "settings.toggle_off":         "⬜ Выключено",

    # system / common
    "system.too_fast":         "🙅 Не топи! ПРИВЫКНИ К АППАРАТУ!",
    "system.unhandled_button": "⚠️ Эта кнопка пока не работает",
    "system.shop_not_found":   "Магазин не найден",
}


PLACEHOLDERS: dict[str, set[str]] = {
    "catalog.found":            {"count"},
    "myshops.list_header":      {"count"},
    "myshops.unsub_all_ask":    {"count"},
    "myshops.unsub_all_done":   {"count"},
    "myshops.sub_all_done":     {"count"},
    "settings.notify_time":     {"time"},
    "settings.pause_active":    {"date"},
    "settings.time_saved":      {"time"},
    "settings.pause_set_done":  {"date"},
    "settings.shop_notif_title":{"shop_name"},
}


DESCRIPTIONS: dict[str, str] = {
    "menu.btn.catalog":  "Кнопка «Каталог» в главном меню",
    "menu.btn.my_shops": "Кнопка «Ну мои» в главном меню",
    "menu.btn.settings": "Кнопка «Настройки» в главном меню",
    "menu.btn.help":     "Кнопка «Помощь» в главном меню",
    "menu.btn.admin":    "Кнопка «Админка» в главном меню (видят только админы)",

    "start.welcome": "Текст приветствия по команде /start и кнопке «В меню»",
    "help.text":     "Текст справки по команде /help и кнопке «❓ Помощь»",

    "catalog.title":          "Заголовок экрана каталога",
    "catalog.found":          "Подпись «Найдено: N» под заголовком каталога",
    "catalog.hint":           "Подсказка под списком в каталоге",
    "catalog.legend":         "Пояснение значков (🚚/🔴/🟡/🟢/📌) под списком каталога",
    "catalog.empty":          "Текст когда в каталоге нет магазинов",
    "catalog.empty_filter":   "Текст когда фильтр/поиск не дали результатов",
    "catalog.shop_not_found": "Алерт когда пользователь открыл удалённый магазин",
    "catalog.more_title":     "Заголовок экрана «Дополнительные фильтры»",
    "catalog.sort_title":     "Заголовок экрана «Сортировка»",
    "catalog.search.prompt":  "Подсказка на экране ввода поискового запроса",

    "myshops.title":           "Заголовок раздела «Ну мои»",
    "myshops.empty":           "Текст когда у пользователя нет отслеживаемых магазинов",
    "myshops.list_header":     "Шапка списка отслеживаемых магазинов",
    "myshops.added":           "Тост после добавления магазина в отслеживаемые",
    "myshops.removed":         "Тост после удаления магазина из отслеживаемых",
    "myshops.unsub_all_ask":   "Подтверждение «отписаться от всех»",
    "myshops.unsub_all_done":  "Тост после массовой отписки",
    "myshops.sub_all_done":    "Тост после массовой подписки",
    "myshops.sub_all_already": "Тост если пользователь уже подписан на всё",

    "settings.title":            "Заголовок экрана настроек",
    "settings.notify_time":      "Строка с текущим временем уведомлений",
    "settings.pause_active":     "Строка когда активна пауза",
    "settings.pause_off":        "Строка когда уведомления включены",
    "settings.time_picker":      "Подсказка на экране выбора времени",
    "settings.time_saved":       "Тост после сохранения времени",
    "settings.pause_picker":     "Подсказка на экране выбора длительности паузы",
    "settings.pause_off_done":   "Тост после снятия паузы",
    "settings.pause_set_done":   "Тост после установки паузы",
    "settings.shop_notif_title": "Заголовок экрана настроек уведомлений магазина",
    "settings.shop_notif_cycle": "Подсказка для магазина с циклом",
    "settings.shop_notif_wdays": "Подсказка для магазина без цикла (дни недели)",
    "settings.need_track":       "Алерт «надо сначала отслеживать»",
    "settings.toggle_on":        "Тост-подтверждение «Включено»",
    "settings.toggle_off":       "Тост-подтверждение «Выключено»",

    "system.too_fast":         "Тост при слишком частых нажатиях",
    "system.unhandled_button": "Тост когда кнопка не обрабатывается (баг)",
    "system.shop_not_found":   "Алерт «магазин не найден» в общих местах",
}


GROUPS: list[tuple[str, str, list[str]]] = [
    ("menu",    "Главное меню", [
        "menu.btn.catalog", "menu.btn.my_shops", "menu.btn.settings",
        "menu.btn.help", "menu.btn.admin",
    ]),
    ("start",   "Старт и помощь", [
        "start.welcome", "help.text",
    ]),
    ("catalog", "Каталог", [
        "catalog.title", "catalog.found", "catalog.hint", "catalog.legend",
        "catalog.empty", "catalog.empty_filter", "catalog.shop_not_found",
        "catalog.more_title", "catalog.sort_title", "catalog.search.prompt",
    ]),
    ("myshops", "Мои магазины", [
        "myshops.title", "myshops.empty", "myshops.list_header",
        "myshops.added", "myshops.removed",
        "myshops.unsub_all_ask", "myshops.unsub_all_done",
        "myshops.sub_all_done", "myshops.sub_all_already",
    ]),
    ("settings","Настройки", [
        "settings.title", "settings.notify_time",
        "settings.pause_active", "settings.pause_off",
        "settings.time_picker", "settings.time_saved",
        "settings.pause_picker", "settings.pause_off_done", "settings.pause_set_done",
        "settings.shop_notif_title", "settings.shop_notif_cycle", "settings.shop_notif_wdays",
        "settings.need_track", "settings.toggle_on", "settings.toggle_off",
    ]),
    ("system",  "Обслуживание и ошибки", [
        "system.too_fast", "system.unhandled_button", "system.shop_not_found",
    ]),
]


# Sample values for live preview (when admin edits text — bot renders preview with these).
PREVIEW_CTX: dict[str, Any] = {
    "username":  "Андрей",
    "count":     "5",
    "time":      "09:00",
    "date":      "31.05.2026",
    "shop_name": "Секонд на Большой Морской",
}


# ---------- runtime ----------

async def t(key: str, **kwargs: Any) -> str:
    """Get the rendered text for a key, with optional placeholder substitutions."""
    if key not in DEFAULTS:
        log.error("unknown text key: %s", key)
        return f"[{key}]"
    raw = await _get_raw(key)
    if not kwargs:
        return raw
    try:
        return raw.format(**kwargs)
    except (KeyError, IndexError) as exc:
        log.warning("texts: format failed for %s: %s — falling back to default", key, exc)
        try:
            return DEFAULTS[key].format(**kwargs)
        except Exception:
            return DEFAULTS[key]


async def _get_raw(key: str) -> str:
    now = time.time()
    cached = _CACHE.get(key)
    if cached and now - cached[0] < _TTL:
        return cached[1]
    async with pool().acquire() as conn:
        row = await conn.fetchrow("SELECT value FROM bot_texts WHERE key = $1", key)
    val = row["value"] if row else DEFAULTS[key]
    _CACHE[key] = (now, val)
    return val


async def warm_cache() -> None:
    """Load every text key in a single query.
    Call once at bot startup so /start, menus, etc. don't fire per-key SELECTs.
    """
    now = time.time()
    async with pool().acquire() as conn:
        rows = await conn.fetch("SELECT key, value FROM bot_texts")
    overrides = {r["key"]: r["value"] for r in rows}
    for key, default in DEFAULTS.items():
        _CACHE[key] = (now, overrides.get(key, default))


def invalidate(key: str | None = None) -> None:
    if key is None:
        _CACHE.clear()
    else:
        _CACHE.pop(key, None)


# ---------- validation ----------

def extract_placeholders(text: str) -> set[str]:
    return set(_VAR_RE.findall(text))


def validate(key: str, new_value: str) -> tuple[bool, str]:
    """Returns (ok, error). Used before saving a new value."""
    if key not in DEFAULTS:
        return False, "Неизвестный ключ."
    if not new_value or not new_value.strip():
        return False, "Текст пустой."
    if len(new_value) > _MAX_LEN:
        return False, f"Слишком длинно ({len(new_value)} > {_MAX_LEN} символов)."
    allowed = PLACEHOLDERS.get(key, set())
    found = extract_placeholders(new_value)
    extra = found - allowed
    if extra:
        if allowed:
            return False, (
                f"Неизвестные подстановки: {', '.join('{'+x+'}' for x in sorted(extra))}. "
                f"Можно: {', '.join('{'+x+'}' for x in sorted(allowed))}."
            )
        return False, (
            f"В этом тексте подстановок быть не должно. "
            f"Ты написал: {', '.join('{'+x+'}' for x in sorted(extra))}."
        )
    # Try a sample render to make sure str.format won't crash.
    try:
        fake = {p: "x" for p in allowed}
        new_value.format(**fake)
    except Exception as exc:
        return False, f"Ошибка форматирования: {exc!s}"
    return True, ""


def render_preview(key: str, value: str) -> str:
    """Render a sample using PREVIEW_CTX (used in the editing flow)."""
    allowed = PLACEHOLDERS.get(key, set())
    ctx = {p: PREVIEW_CTX.get(p, p) for p in allowed}
    try:
        return value.format(**ctx) if ctx else value
    except Exception:
        return value
