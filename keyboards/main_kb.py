from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from services.texts import t


async def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=await t("menu.btn.catalog"),  callback_data="catalog:open")],
        [InlineKeyboardButton(text=await t("menu.btn.my_shops"), callback_data="my_shops:open")],
        [InlineKeyboardButton(text=await t("menu.btn.settings"), callback_data="settings:open")],
        #[InlineKeyboardButton(text=await t("menu.btn.help"),     callback_data="help:open")],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton(text=await t("menu.btn.admin"), callback_data="admin:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_menu() -> InlineKeyboardMarkup:
    from handlers.admin.shops import ShopCb

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🆕 Добавить магазин", callback_data="adm:addshop:start")],
            [InlineKeyboardButton(
                text="📋 Список магазинов",
                callback_data=ShopCb(action="list", page=0).pack(),
            )],
            [InlineKeyboardButton(text="📊 Статистика", callback_data="adm:stats")],
            [InlineKeyboardButton(text="🚚 Импорт Megahand", callback_data="adm:par:megahand")],
            [InlineKeyboardButton(text="📣 Рассылка", callback_data="adm:bc:menu")],
            [InlineKeyboardButton(text="🏠 В меню", callback_data="menu:open")],
        ]
    )
