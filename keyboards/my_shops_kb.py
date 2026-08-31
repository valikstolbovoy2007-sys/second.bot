from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from data.repos.shops import Shop

PAGE_SIZE = 6


class MyShopsCb(CallbackData, prefix="my"):
    action: str  # "list" | "shop" | "sub_all" | "unsub_all_ask" | "unsub_all_yes"
    page: int = 0
    shop_id: int = 0


def _shop_button_label(shop: Shop, today_marker: str = "") -> str:
    prefix = today_marker + " " if today_marker else ""
    name = shop.name
    if shop.chain_name and shop.chain_name not in name:
        name = f"{shop.chain_name}: {name}"
    return f"{prefix}{name}"[:60]


def my_shops_kb(
    shops: list[Shop],
    page: int,
    total: int,
    today_markers: dict[int, str] | None = None,
) -> InlineKeyboardMarkup:
    today_markers = today_markers or {}
    rows: list[list[InlineKeyboardButton]] = []

    for shop in shops:
        rows.append([
            InlineKeyboardButton(
                text=_shop_button_label(shop, today_markers.get(shop.id, "")),
                callback_data=MyShopsCb(action="shop", page=page, shop_id=shop.id).pack(),
            )
        ])

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            text="◀️",
            callback_data=MyShopsCb(action="list", page=page - 1).pack(),
        ))
    last_page = max(0, (total - 1) // PAGE_SIZE)
    nav.append(InlineKeyboardButton(
        text=f"{page + 1}/{last_page + 1}",
        callback_data="noop",
    ))
    if page < last_page:
        nav.append(InlineKeyboardButton(
            text="▶️",
            callback_data=MyShopsCb(action="list", page=page + 1).pack(),
        ))
    if nav:
        rows.append(nav)

    rows.append([
        InlineKeyboardButton(
            text="➕ Подписаться на все",
            callback_data=MyShopsCb(action="sub_all").pack(),
        ),
    ])
    if total > 0:
        rows.append([
            InlineKeyboardButton(
                text="🗑 Отписаться от всех",
                callback_data=MyShopsCb(action="unsub_all_ask").pack(),
            ),
        ])
    rows.append([InlineKeyboardButton(text="🏠 В меню", callback_data="menu:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_unsub_all_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Да, отписаться",
                callback_data=MyShopsCb(action="unsub_all_yes").pack(),
            ),
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data=MyShopsCb(action="list", page=0).pack(),
            ),
        ]
    ])
