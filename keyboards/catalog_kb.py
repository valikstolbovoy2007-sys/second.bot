from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from data.repos.shops import Shop
from services.catalog import (
    FLT_ALL,
    FLT_BY_PRICE,
    FLT_BY_WEIGHT,
    FLT_TRACKED,
    MORE_FILTERS,
    SORT_ARRIVAL,
    SORT_NAME,
    SORT_PRICE,
)

PAGE_SIZE = 6

# Filter button labels (used both on main keyboard and on the "More" sub-screen).
FILTER_LABELS: dict[str, str] = {
    FLT_ALL:             "Все",
    FLT_TRACKED:         "💘 Ну мои",
    FLT_BY_WEIGHT:       "⚖️ По весу",
    FLT_BY_PRICE:        "📌 По цене",
}

# Short label for the header (when a non-default filter is active).
FILTER_SHORT: dict[str, str] = {
    FLT_TRACKED:         "💘 Ну мои",
    FLT_BY_WEIGHT:       "⚖️ По весу",
    FLT_BY_PRICE:        "📌 По цене",
}

SORT_LABELS: dict[str, str] = {
    SORT_NAME:    "По названию",
    SORT_ARRIVAL: "По дню завоза",
    SORT_PRICE:   "По цене сегодня",
}


class CatalogCb(CallbackData, prefix="cat"):
    # action: list | shop | filter | more | sort_open | sort_pick |
    #         search_start | search_clear | reset | sched
    action: str
    page: int = 0
    flt: str = FLT_ALL
    sort: str = SORT_NAME
    shop_id: int = 0
    # Telegram decodes an empty trailing callback segment as None.  This field
    # is only populated for sort_pick, so accept both forms for other actions.
    value: str | None = ""  # for sort_pick / filter codes too long for `flt`


class TrackCb(CallbackData, prefix="trk"):
    shop_id: int
    src: str = "cat"   # "cat" | "my"
    page: int = 0
    flt: str = FLT_ALL
    sort: str = SORT_NAME


def _shop_button_label(shop: Shop, phase_marker: str = "", tracked: bool = False) -> str:
    markers = "".join(m for m in (("" if shop.price_start else "📌"), phase_marker) if m)
    prefix = markers + " " if markers else ""
    star = "💘 " if tracked else ""
    name = shop.name
    if shop.chain_name and shop.chain_name not in name:
        name = f"{shop.chain_name}: {name}"
    return f"{prefix}{star}{name}"[:60]


def catalog_kb(
    shops: list[Shop],
    page: int,
    flt: str,
    sort: str,
    total: int,
    *,
    has_search: bool,
    phase_markers: dict[int, str] | None = None,
    tracked_ids: set[int] | None = None,
) -> InlineKeyboardMarkup:
    phase_markers = phase_markers or {}
    tracked_ids = tracked_ids or set()
    rows: list[list[InlineKeyboardButton]] = []

    for shop in shops:
        rows.append([
            InlineKeyboardButton(
                text=_shop_button_label(
                    shop,
                    phase_markers.get(shop.id, ""),
                    tracked=shop.id in tracked_ids,
                ),
                callback_data=CatalogCb(
                    action="shop", page=page, flt=flt, sort=sort, shop_id=shop.id,
                ).pack(),
            )
        ])

    nav: list[InlineKeyboardButton] = []
    last_page = max(0, (total - 1) // PAGE_SIZE)
    if page > 0:
        nav.append(InlineKeyboardButton(
            text="◀️",
            callback_data=CatalogCb(
                action="list", page=page - 1, flt=flt, sort=sort,
            ).pack(),
        ))
    nav.append(InlineKeyboardButton(
        text=f"{page + 1}/{last_page + 1}",
        callback_data="noop",
    ))
    if page < last_page:
        nav.append(InlineKeyboardButton(
            text="▶️",
            callback_data=CatalogCb(
                action="list", page=page + 1, flt=flt, sort=sort,
            ).pack(),
        ))
    if nav:
        rows.append(nav)

    # Tools row: search + sort.
    rows.append([
        InlineKeyboardButton(
            text="🔍 Поиск",
            callback_data=CatalogCb(action="search_start", flt=flt, sort=sort).pack(),
        ),
        InlineKeyboardButton(
            text=f"↕ {SORT_LABELS[sort]}",
            callback_data=CatalogCb(action="sort_open", flt=flt, sort=sort).pack(),
        ),
    ])

    # Primary filters: All | 💘Ну мои | ⋯ Ещё.
    primary_row: list[InlineKeyboardButton] = []
    for code in (FLT_ALL, FLT_TRACKED):
        text = FILTER_LABELS[code]
        if code == flt:
            text = f"• {text} •"
        primary_row.append(InlineKeyboardButton(
            text=text,
            callback_data=CatalogCb(action="filter", flt=code, sort=sort).pack(),
        ))
    # "More" highlighted if a "more" filter is active.
    more_text = "⋯ Ещё"
    if flt in MORE_FILTERS:
        more_text = f"• {FILTER_LABELS[flt]} •"
    primary_row.append(InlineKeyboardButton(
        text=more_text,
        callback_data=CatalogCb(action="more", flt=flt, sort=sort).pack(),
    ))
    rows.append(primary_row)

    # Reset row — only if anything is active.
    if flt != FLT_ALL or sort != SORT_NAME or has_search:
        rows.append([InlineKeyboardButton(
            text="✖️ Сбросить",
            callback_data=CatalogCb(action="reset").pack(),
        )])

    rows.append([InlineKeyboardButton(text="🏠 В меню", callback_data="menu:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def empty_filter_kb(flt: str, has_search: bool) -> InlineKeyboardMarkup:
    """Shown when filter/search returns zero matches."""
    rows: list[list[InlineKeyboardButton]] = []
    if flt != FLT_ALL or has_search:
        rows.append([InlineKeyboardButton(
            text="✖️ Сбросить фильтр",
            callback_data=CatalogCb(action="reset").pack(),
        )])
    rows.append([InlineKeyboardButton(text="🏠 В меню", callback_data="menu:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def more_filters_kb(flt: str, sort: str) -> InlineKeyboardMarkup:
    """Sub-screen for extra filters (⚖️ по весу / 📌 по цене)."""
    rows: list[list[InlineKeyboardButton]] = []
    for code in MORE_FILTERS:
        text = FILTER_LABELS[code]
        if code == flt:
            text = f"• {text} •"
        rows.append([InlineKeyboardButton(
            text=text,
            callback_data=CatalogCb(action="filter", flt=code, sort=sort).pack(),
        )])
    rows.append([InlineKeyboardButton(
        text="◀️ Назад",
        callback_data=CatalogCb(action="list", flt=flt, sort=sort).pack(),
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def sort_kb(flt: str, sort: str) -> InlineKeyboardMarkup:
    """Sub-screen for sort selection."""
    rows: list[list[InlineKeyboardButton]] = []
    for code, label in SORT_LABELS.items():
        text = label
        if code == sort:
            text = f"• {label} •"
        rows.append([InlineKeyboardButton(
            text=text,
            callback_data=CatalogCb(action="sort_pick", flt=flt, sort=sort, value=code).pack(),
        )])
    rows.append([InlineKeyboardButton(
        text="◀️ Назад",
        callback_data=CatalogCb(action="list", flt=flt, sort=sort).pack(),
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def search_cancel_kb(flt: str, sort: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="✖️ Отмена",
            callback_data=CatalogCb(action="list", flt=flt, sort=sort).pack(),
        ),
    ]])


def shop_card_kb(
    shop_id: int,
    is_tracked: bool,
    src: str,
    page: int,
    flt: str = FLT_ALL,
    sort: str = SORT_NAME,
    has_prices: bool = False,
    maps_url: str = "",
    on_schedule: bool = False,
) -> InlineKeyboardMarkup:
    """Клавиатура карточки магазина.

    `maps_url` — готовая ссылка на Яндекс.Карты (см. services.maps).
    Если пустая — кнопка не показывается. Это фолбэк к ссылке-адресу
    в теле карточки: на десктопе/в редких клиентах HTML-ссылка может
    не открыться, а url-кнопка работает везде.

    `on_schedule` — True когда уже открыт экран «Расписание цен» для этого
    магазина: сама кнопка перехода туда тогда не нужна.
    """
    track_text = "❌ Не отслеживать" if is_tracked else "💘 Отслеживать"
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(
            text=track_text,
            callback_data=TrackCb(
                shop_id=shop_id, src=src, page=page, flt=flt, sort=sort,
            ).pack(),
        )],
    ]
    if has_prices and not on_schedule:
        rows.append([InlineKeyboardButton(
            text="📋 Расписание цен",
            callback_data=CatalogCb(
                action="sched", page=page, flt=flt, sort=sort, shop_id=shop_id,
            ).pack(),
        )])
    rows.append([InlineKeyboardButton(
        text="⚒️ Исправить неточность",
        callback_data=CatalogCb(
            action="report", page=page, flt=flt, sort=sort, shop_id=shop_id,
        ).pack(),
    )])
    if is_tracked:
        from keyboards.settings_kb import ShopNotifCb
        rows.append([InlineKeyboardButton(
            text="⚙️ Настройки уведомлений",
            callback_data=ShopNotifCb(
                shop_id=shop_id, action="open", src=src, page=page,
            ).pack(),
        )])
    if src == "my":
        from keyboards.my_shops_kb import MyShopsCb
        rows.append([InlineKeyboardButton(
            text="◀️ К моим магазинам",
            callback_data=MyShopsCb(action="list", page=page).pack(),
        )])
    else:
        rows.append([InlineKeyboardButton(
            text="◀️ К каталогу",
            callback_data=CatalogCb(
                action="list", page=page, flt=flt, sort=sort,
            ).pack(),
        )])
    rows.append([InlineKeyboardButton(text="🏠 В меню", callback_data="menu:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
