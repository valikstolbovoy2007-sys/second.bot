"""Extra shop ops: search, photo gallery, bulk activate/deactivate (TZ §3)."""
import html
import logging
from datetime import date

from aiogram import F, Router
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)

from data.db import pool
from data.repos.admin_roles import (
    can_access_shop,
    is_super_admin,
    visible_shop_ids,
)
from data.repos.shop_photos import (
    add_photo,
    count_photos,
    get_photo,
    list_photos,
    remove_photo,
)
from data.repos.shops import get_shop, list_shops_scoped
from handlers.admin.filters import IsAdmin
from handlers.admin.shops import ShopCb
from handlers.admin.ui import safe_edit
from services.audit import write as audit_write
from states.admin_states import ShopPhotoStates, ShopSearchStates

log = logging.getLogger(__name__)
router = Router(name="admin_shops_extra")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


class ShopXCb(CallbackData, prefix="admshx"):
    action: str
    shop_id: int = 0
    photo_id: int = 0
    value: str | None = None


# ---------- search ----------

@router.callback_query(F.data == "adm:shops:search")
async def cb_search_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ShopSearchStates.query)
    await call.message.answer("🔎 Введи часть имени или адреса:")
    await call.answer()


@router.message(ShopSearchStates.query, F.text)
async def msg_search_query(message: Message, state: FSMContext) -> None:
    raw = message.text.strip()
    if raw == "/cancel":
        await state.clear()
        await message.answer("Отменено.")
        return
    await state.clear()
    scope = await visible_shop_ids(message.from_user.id)
    items, total = await list_shops_scoped(scope, 30, 0, search=raw)
    if not items:
        await message.answer("Ничего не нашёл.")
        return
    rows = []
    for s in items:
        chain = f"[{s.chain_name}] " if s.chain_name else ""
        flag = "" if s.is_active else " 🚫"
        rows.append([InlineKeyboardButton(
            text=f"{chain}{s.name}{flag}",
            callback_data=ShopCb(action="card", shop_id=s.id, page=0).pack(),
        )])
    rows.append([InlineKeyboardButton(text="← Назад", callback_data=ShopCb(action="list", page=0).pack())])
    await message.answer(
        f"🔎 Найдено: <b>{total}</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


# ---------- photos ----------

@router.callback_query(ShopXCb.filter(F.action == "photos"))
async def cb_photos(call: CallbackQuery, callback_data: ShopXCb) -> None:
    if not await can_access_shop(call.from_user.id, callback_data.shop_id):
        await call.answer("Нет доступа", show_alert=True)
        return
    photos = await list_photos(callback_data.shop_id)
    shop = await get_shop(callback_data.shop_id)
    if not shop:
        await call.answer("Магазин не найден", show_alert=True)
        return
    rows: list[list[InlineKeyboardButton]] = []
    if photos:
        # send the gallery as a media group, then return navigation
        media = [InputMediaPhoto(media=p["file_id"]) for p in photos[:10]]
        try:
            await call.message.answer_media_group(media)
        except Exception:
            log.exception("send media group failed")
        for p in photos:
            rows.append([InlineKeyboardButton(
                text=f"🗑 #{p['id']}",
                callback_data=ShopXCb(action="delphoto", shop_id=callback_data.shop_id, photo_id=p["id"]).pack(),
            )])
    else:
        await call.message.answer("Фотографий нет.")
    rows.append([InlineKeyboardButton(
        text="📷 Загрузить фото",
        callback_data=ShopXCb(action="addphoto", shop_id=callback_data.shop_id).pack(),
    )])
    rows.append([InlineKeyboardButton(
        text="← К карточке",
        callback_data=ShopCb(action="card", shop_id=callback_data.shop_id, page=0).pack(),
    )])
    await call.message.answer(
        f"📷 <b>Фото магазина</b> «{html.escape(shop.name)}» — {len(photos)} шт.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await call.answer()


@router.callback_query(ShopXCb.filter(F.action == "addphoto"))
async def cb_addphoto(call: CallbackQuery, callback_data: ShopXCb, state: FSMContext) -> None:
    if not await can_access_shop(call.from_user.id, callback_data.shop_id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(ShopPhotoStates.upload)
    await state.update_data(shop_id=callback_data.shop_id)
    await call.message.answer(
        "📷 Пришли фотографию (по одной за раз). /cancel — выйти."
    )
    await call.answer()


@router.message(ShopPhotoStates.upload, F.photo)
async def msg_addphoto(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    shop_id = data.get("shop_id")
    if not shop_id:
        await state.clear()
        await message.answer("Битый стейт.")
        return
    if not await can_access_shop(message.from_user.id, shop_id):
        await state.clear()
        await message.answer("Нет доступа.")
        return
    file_id = message.photo[-1].file_id
    n = await count_photos(shop_id)
    if n >= 20:
        await message.answer("Лимит — 20 фото на магазин.")
        return
    photo_id = await add_photo(shop_id, file_id, message.from_user.id)
    await audit_write(
        message.from_user.id, "shop.photo.add", "shop", shop_id,
        {"photo_id": photo_id},
    )
    await message.answer(f"✅ Загружено фото #{photo_id}. Можешь прислать ещё или /cancel.")


@router.message(ShopPhotoStates.upload, F.text == "/cancel")
async def msg_addphoto_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Готово.")


@router.callback_query(ShopXCb.filter(F.action == "delphoto"))
async def cb_delphoto(call: CallbackQuery, callback_data: ShopXCb) -> None:
    if not await can_access_shop(call.from_user.id, callback_data.shop_id):
        await call.answer("Нет доступа", show_alert=True)
        return
    photo = await get_photo(callback_data.photo_id)
    if not photo or photo["shop_id"] != callback_data.shop_id:
        await call.answer("Не найдено", show_alert=True)
        return
    await remove_photo(callback_data.photo_id)
    await audit_write(
        call.from_user.id, "shop.photo.remove", "shop", callback_data.shop_id,
        {"photo_id": callback_data.photo_id},
    )
    await call.answer("Удалено")


# ---------- bulk ops (super only) ----------

@router.callback_query(F.data == "adm:shops:bulk")
async def cb_bulk_menu(call: CallbackQuery) -> None:
    if not await is_super_admin(call.from_user.id):
        await call.answer("Только супер-админ", show_alert=True)
        return
    rows = [
        [InlineKeyboardButton(
            text="✅ Активировать всю сеть",
            callback_data=ShopXCb(action="bulkchain", value="act").pack(),
        )],
        [InlineKeyboardButton(
            text="🚫 Деактивировать всю сеть",
            callback_data=ShopXCb(action="bulkchain", value="deact").pack(),
        )],
        [InlineKeyboardButton(
            text="🗓 Установить anchor сети",
            callback_data=ShopXCb(action="bulkchain", value="anchor").pack(),
        )],
        [InlineKeyboardButton(text="← Назад", callback_data="adm:back")],
    ]
    await safe_edit(call, "🧰 <b>Массовые операции по сети</b>",
                    InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


@router.callback_query(ShopXCb.filter(F.action == "bulkchain"))
async def cb_bulk_chain_pick(call: CallbackQuery, callback_data: ShopXCb) -> None:
    if not await is_super_admin(call.from_user.id):
        await call.answer("Только супер-админ", show_alert=True)
        return
    op = callback_data.value
    async with pool().acquire() as conn:
        chains = await conn.fetch(
            "SELECT DISTINCT chain_name FROM shops WHERE chain_name IS NOT NULL ORDER BY chain_name"
        )
    if not chains:
        await call.answer("Нет сетей", show_alert=True)
        return
    rows = [[InlineKeyboardButton(
        text=c["chain_name"],
        callback_data=ShopXCb(action=f"bulk_{op}", value=c["chain_name"]).pack(),
    )] for c in chains]
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="adm:shops:bulk")])
    await safe_edit(call, f"Выбери сеть для операции «{op}»:",
                    InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


async def _bulk_set_active(chain: str, value: bool, actor_tg_id: int) -> int:
    async with pool().acquire() as conn:
        result = await conn.execute(
            "UPDATE shops SET is_active = $2 WHERE chain_name = $1",
            chain, value,
        )
    n = int(result.split()[-1]) if result.startswith("UPDATE") else 0
    await audit_write(
        actor_tg_id, "shop.bulk.is_active", "chain", chain,
        {"value": value, "updated": n},
    )
    return n


@router.callback_query(ShopXCb.filter(F.action == "bulk_act"))
async def cb_bulk_act(call: CallbackQuery, callback_data: ShopXCb) -> None:
    if not await is_super_admin(call.from_user.id):
        await call.answer("Только супер-админ", show_alert=True)
        return
    n = await _bulk_set_active(callback_data.value, True, call.from_user.id)
    await call.answer(f"Активировано {n}", show_alert=True)


@router.callback_query(ShopXCb.filter(F.action == "bulk_deact"))
async def cb_bulk_deact(call: CallbackQuery, callback_data: ShopXCb) -> None:
    if not await is_super_admin(call.from_user.id):
        await call.answer("Только супер-админ", show_alert=True)
        return
    n = await _bulk_set_active(callback_data.value, False, call.from_user.id)
    await call.answer(f"Деактивировано {n}", show_alert=True)


@router.callback_query(ShopXCb.filter(F.action == "bulk_anchor"))
async def cb_bulk_anchor_today(call: CallbackQuery, callback_data: ShopXCb) -> None:
    if not await is_super_admin(call.from_user.id):
        await call.answer("Только супер-админ", show_alert=True)
        return
    chain = callback_data.value
    today = date.today()
    async with pool().acquire() as conn:
        result = await conn.execute(
            "UPDATE shops SET anchor_date = $2 WHERE chain_name = $1",
            chain, today,
        )
    n = int(result.split()[-1]) if result.startswith("UPDATE") else 0
    await audit_write(
        call.from_user.id, "shop.bulk.anchor", "chain", chain,
        {"anchor": today.isoformat(), "updated": n},
    )
    await call.answer(f"anchor=сегодня для {n} магазинов", show_alert=True)
