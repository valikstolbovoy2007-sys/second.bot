import html
import logging
from datetime import date, datetime

from aiogram import F, Router
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from data.repos.admin_roles import (
    can_access_shop,
    is_super_admin,
    visible_shop_ids,
)
from data.repos.shops import (
    get_shop,
    list_shops_scoped,
    shop_subscribers_count,
    update_shop_field,
)
from handlers.admin.filters import IsAdmin
from handlers.admin.ui import safe_edit
from services.audit import write as audit_write
from services.cycle import EventType, OCCURRENCE_LAST, next_event_date, resolve_cycle_info

log = logging.getLogger(__name__)
router = Router(name="admin_shops")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

PAGE_SIZE = 10
FIELDS = {
    "name": ("Название", 120),
    "address": ("Адрес", 200),
    "description": ("Описание", 2000),
    "cycle": ("Цикл (дней)", 10),
    "anchor": ("Дата завоза (YYYY-MM-DD)", 10),
    "price_start": ("Цена в день завоза (₽/кг)", 10),
    "price_step": ("Шаг снижения цены (₽/день)", 10),
    # Свободный текст «Пн-Сб 10:00–21:00, Вс выходной». Спецобработки нет
    # — пишется в БД as-is. Лимит совпадает с MAX_WORKING_HOURS в визарде.
    "working_hours": ("Время работы", 100),
    # Сеть. Принимает любой текст; «-» / «—» / пусто = сделать магазин
    # независимым (NULL в БД). Длина 50 — с запасом на новые сети.
    "chain_name": ("Сеть (или «-» для независимого)", 50),
}


class ShopCb(CallbackData, prefix="admshops"):
    action: str       # list, card, edit, save, deact, act, delete, deletec,
                      # anchor_input (ввести дату), anchor_days (по неделе в месяце),
                      # cancel_edit (удалить временное окно редактирования)
    page: int = 0
    shop_id: int = 0
    field: str | None = None


class AnchorWdCb(CallbackData, prefix="admanchwd"):
    """Step 1 — pick the weekday; step 2 (AnchorOccCb) picks which occurrence."""
    shop_id: int
    page: int = 0
    wd: int = 0  # 0=Пн ... 6=Вс


class AnchorOccCb(CallbackData, prefix="admanchocc"):
    shop_id: int
    page: int = 0
    wd: int = 0
    occurrence: int = 1  # 1..5 = n-я неделя месяца, OCCURRENCE_LAST = последняя


_OCCURRENCE_LABELS = [(1, "1-я"), (2, "2-я"), (3, "3-я"), (4, "4-я"), (5, "5-я"),
                      (OCCURRENCE_LAST, "Последняя")]


_WEEKDAY_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


class EditStates(StatesGroup):
    value = State()


# ---------- LIST ----------

def _list_kb(items: list, page: int, total: int, is_super: bool = False) -> InlineKeyboardMarkup:
    rows = []
    for s in items:
        chain = f"[{s.chain_name}] " if s.chain_name else ""
        flag = "" if s.is_active else " 🚫"
        rows.append([InlineKeyboardButton(
            text=f"{chain}{s.name}{flag}",
            callback_data=ShopCb(action="card", shop_id=s.id, page=page).pack(),
        )])
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(
                text="◀️", callback_data=ShopCb(action="list", page=page - 1).pack()
            ))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="noop"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton(
                text="▶️", callback_data=ShopCb(action="list", page=page + 1).pack()
            ))
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="🔎 Поиск", callback_data="adm:shops:search")])
    if is_super:
        rows.append([InlineKeyboardButton(
            text="🆕 Добавить магазин",
            callback_data="adm:addshop:start",
        )])
        rows.append([InlineKeyboardButton(
            text="🧰 Массовые операции",
            callback_data="adm:shops:bulk",
        )])
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="adm:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(ShopCb.filter(F.action == "list"))
async def cb_list(call: CallbackQuery, callback_data: ShopCb) -> None:
    page = max(0, callback_data.page)
    scope = await visible_shop_ids(call.from_user.id)
    items, total = await list_shops_scoped(scope, PAGE_SIZE, page * PAGE_SIZE)
    is_super = scope is None
    title = "Все магазины" if is_super else "Мои магазины"
    if total == 0:
        text = (
            f"<b>{title} (0)</b>\n\n"
            + ("Магазинов пока нет." if is_super else
               "Тебе пока не назначен ни один магазин. Попроси супер-админа.")
        )
        await safe_edit(call, text, _list_kb([], 0, 0, is_super))
        await call.answer()
        return
    lines = [f"<b>{title} ({total})</b>", "Кликни магазин для управления."]
    await safe_edit(call, "\n".join(lines), _list_kb(items, page, total, is_super))
    await call.answer()


@router.callback_query(F.data == "noop")
async def cb_noop(call: CallbackQuery) -> None:
    await call.answer()


# ---------- CARD ----------

def _card_kb(shop_id: int, page: int, is_active: bool, is_super: bool) -> InlineKeyboardMarkup:
    from handlers.admin.arrivals import ArrCb
    from handlers.admin.shops_extra import ShopXCb
    edit_btn = lambda label, field: InlineKeyboardButton(
        text=label,
        callback_data=ShopCb(action="edit", shop_id=shop_id, field=field, page=page).pack(),
    )
    rows = [
        [edit_btn("✏️ Имя", "name"), edit_btn("✏️ Адрес", "address")],
        [edit_btn("🏷 Сеть", "chain_name"), edit_btn("🕒 Время работы", "working_hours")],
        [edit_btn("✏️ Описание", "description")],
        [edit_btn("✏️ Цикл", "cycle"), edit_btn("✏️ Anchor", "anchor")],
        [edit_btn("💰 Цена/кг", "price_start"), edit_btn("📉 Шаг ₽/день", "price_step")],
        [
            InlineKeyboardButton(
                text="📷 Фото",
                callback_data=ShopXCb(action="photos", shop_id=shop_id).pack(),
            ),
            InlineKeyboardButton(
                text="📜 История",
                callback_data=ArrCb(action="hist", shop_id=shop_id, page=0).pack(),
            ),
        ],
    ]
    if is_active:
        rows.append([InlineKeyboardButton(
            text="🚫 Деактивировать",
            callback_data=ShopCb(action="deact", shop_id=shop_id, page=page).pack(),
        )])
    else:
        rows.append([InlineKeyboardButton(
            text="✅ Активировать",
            callback_data=ShopCb(action="act", shop_id=shop_id, page=page).pack(),
        )])
    if is_super:
        rows.append([InlineKeyboardButton(
            text="🗑 Удалить",
            callback_data=ShopCb(action="delete", shop_id=shop_id, page=page).pack(),
        )])
        rows.append([InlineKeyboardButton(
            text="👥 Назначенные админы",
            callback_data=f"sa:assignshop:{shop_id}",
        )])
    rows.append([InlineKeyboardButton(
        text="← К списку",
        callback_data=ShopCb(action="list", page=page).pack(),
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _format_card(shop, subs: int) -> str:
    chain = f"[{shop.chain_name}] " if shop.chain_name else ""
    if shop.monthly_weekday is not None:
        occ_label = dict(_OCCURRENCE_LABELS).get(shop.monthly_occurrence, "1-я")
        cycle_anchor = f"{occ_label} {_WEEKDAY_RU[shop.monthly_weekday]} месяца"
    else:
        cycle = f"{shop.cycle_length} дней" if shop.cycle_length else "—"
        anchor = shop.anchor_date.strftime("%d.%m.%Y") if shop.anchor_date else "—"
        cycle_anchor = f"{cycle} | Anchor: {anchor}"
    flag = "✅ активен" if shop.is_active else "🚫 неактивен"
    desc = html.escape(shop.description) if shop.description else "—"
    hours = html.escape(shop.working_hours) if shop.working_hours else "—"
    price_line = "💰 Цена: —"
    if shop.price_start and shop.price_step is not None:
        price_line = f"💰 {shop.price_start} ₽/кг в день завоза · шаг {shop.price_step} ₽/день"
    elif shop.price_start:
        price_line = f"💰 {shop.price_start} ₽/кг (шаг не задан)"
    return (
        f"🛍 <b>{chain}{html.escape(shop.name)}</b>  (id={shop.id})\n"
        f"{flag}\n"
        f"📍 {html.escape(shop.address)}\n"
        f"🕒 Время работы: {hours}\n"
        f"🗓 Цикл: {cycle_anchor}\n"
        f"{price_line}\n"
        f"👀 Подписчиков: {subs}\n"
        f"─────────────────────\n"
        f"{desc}"
    )


async def _show_card(call: CallbackQuery, shop_id: int, page: int) -> None:
    if not await can_access_shop(call.from_user.id, shop_id):
        await audit_write(call.from_user.id, "access_denied", "shop", shop_id)
        await call.answer("Нет доступа", show_alert=True)
        return
    shop = await get_shop(shop_id)
    if not shop:
        await call.answer("Магазин не найден", show_alert=True)
        return
    subs = await shop_subscribers_count(shop_id)
    is_super = await is_super_admin(call.from_user.id)
    await safe_edit(call, _format_card(shop, subs), _card_kb(shop_id, page, shop.is_active, is_super))
    await call.answer()


@router.callback_query(ShopCb.filter(F.action == "card"))
async def cb_card(call: CallbackQuery, callback_data: ShopCb) -> None:
    await _show_card(call, callback_data.shop_id, callback_data.page)


# ---------- EDIT ----------

@router.callback_query(ShopCb.filter(F.action == "edit"))
async def cb_edit_start(call: CallbackQuery, callback_data: ShopCb, state: FSMContext) -> None:
    if not await can_access_shop(call.from_user.id, callback_data.shop_id):
        await audit_write(call.from_user.id, "access_denied", "shop", callback_data.shop_id)
        await call.answer("Нет доступа", show_alert=True)
        return
    if callback_data.field not in FIELDS:
        await call.answer("Это поле редактируется супер-админом", show_alert=True)
        return
    label, _ = FIELDS[callback_data.field]
    if callback_data.field == "anchor":
        rows = [
            [InlineKeyboardButton(
                text="📅 Ввести дату (YYYY-MM-DD)",
                callback_data=ShopCb(
                    action="anchor_input", shop_id=callback_data.shop_id, page=callback_data.page,
                ).pack(),
            )],
            [InlineKeyboardButton(
                text="🗓 Настройка по неделе в месяце",
                callback_data=ShopCb(
                    action="anchor_days", shop_id=callback_data.shop_id, page=callback_data.page,
                ).pack(),
            )],
            [InlineKeyboardButton(
                text="✖️ Отмена",
                callback_data=ShopCb(action="cancel_edit").pack(),
            )],
        ]
        await call.message.answer(
            "✏️ <b>Anchor</b> — как задать точку отсчёта?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
        await call.answer()
        return
    await state.set_state(EditStates.value)
    await state.update_data(
        shop_id=callback_data.shop_id, field=callback_data.field, page=callback_data.page
    )
    rows = []
    rows.append([InlineKeyboardButton(
        text="✖️ Отмена",
        callback_data=ShopCb(action="card", shop_id=callback_data.shop_id, page=callback_data.page).pack(),
    )])
    prompt = f"Введи новое значение поля «{label}»:"
    await call.message.answer(prompt, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


@router.callback_query(ShopCb.filter(F.action == "anchor_input"))
async def cb_anchor_input_start(call: CallbackQuery, callback_data: ShopCb, state: FSMContext) -> None:
    if not await can_access_shop(call.from_user.id, callback_data.shop_id):
        await audit_write(call.from_user.id, "access_denied", "shop", callback_data.shop_id)
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(EditStates.value)
    await state.update_data(
        shop_id=callback_data.shop_id, field="anchor", page=callback_data.page,
    )
    await call.message.answer(
        "📅 Введи дату завоза в формате <b>YYYY-MM-DD</b> (например 2026-09-15):"
    )
    await call.answer()


@router.callback_query(ShopCb.filter(F.action == "anchor_days"))
async def cb_anchor_days(call: CallbackQuery, callback_data: ShopCb) -> None:
    if not await can_access_shop(call.from_user.id, callback_data.shop_id):
        await audit_write(call.from_user.id, "access_denied", "shop", callback_data.shop_id)
        await call.answer("Нет доступа", show_alert=True)
        return
    rows = []
    for i in range(0, 7, 2):
        row = [
            InlineKeyboardButton(
                text=wd,
                callback_data=AnchorWdCb(
                    shop_id=callback_data.shop_id, page=callback_data.page, wd=i + j,
                ).pack(),
            )
            for j, wd in enumerate(_WEEKDAY_RU[i:i + 2])
        ]
        rows.append(row)
    rows.append([InlineKeyboardButton(
        text="✖️ Отмена",
        callback_data=ShopCb(action="cancel_edit").pack(),
    )])
    await call.message.answer(
        "🗓 Выбери день недели месяца (дальше уточнишь, какая по счёту неделя):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await call.answer()


@router.callback_query(ShopCb.filter(F.action == "cancel_edit"))
async def cb_cancel_edit(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.delete()
    await call.answer()


@router.callback_query(AnchorWdCb.filter())
async def cb_anchor_pick_weekday(call: CallbackQuery, callback_data: AnchorWdCb) -> None:
    if not await can_access_shop(call.from_user.id, callback_data.shop_id):
        await audit_write(call.from_user.id, "access_denied", "shop", callback_data.shop_id)
        await call.answer("Нет доступа", show_alert=True)
        return
    rows = [
        [InlineKeyboardButton(
            text=label,
            callback_data=AnchorOccCb(
                shop_id=callback_data.shop_id, page=callback_data.page,
                wd=callback_data.wd, occurrence=occ,
            ).pack(),
        )]
        for occ, label in _OCCURRENCE_LABELS
    ]
    rows.append([InlineKeyboardButton(
        text="✖️ Отмена",
        callback_data=ShopCb(action="cancel_edit").pack(),
    )])
    await call.message.answer(
        f"Какая по счёту {_WEEKDAY_RU[callback_data.wd]} месяца?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await call.answer()


_OCCURRENCE_RU = {1: "1-ю", 2: "2-ю", 3: "3-ю", 4: "4-ю", 5: "5-ю", OCCURRENCE_LAST: "последнюю"}


@router.callback_query(AnchorOccCb.filter())
async def cb_anchor_pick_occurrence(call: CallbackQuery, callback_data: AnchorOccCb, state: FSMContext) -> None:
    if not await can_access_shop(call.from_user.id, callback_data.shop_id):
        await audit_write(call.from_user.id, "access_denied", "shop", callback_data.shop_id)
        await call.answer("Нет доступа", show_alert=True)
        return
    shop_before = await get_shop(callback_data.shop_id)
    # monthly_weekday takes over from the fixed cycle entirely — the actual
    # anchor/cycle_length get recomputed live from today() on every render
    # (see services.cycle.resolve_cycle_info), so the stored ones would
    # just be stale leftovers.
    ok = await update_shop_field(callback_data.shop_id, "monthly_weekday", callback_data.wd)
    if ok:
        await update_shop_field(callback_data.shop_id, "monthly_occurrence", callback_data.occurrence)
        await update_shop_field(callback_data.shop_id, "cycle_length", None)
        await update_shop_field(callback_data.shop_id, "anchor_date", None)
    if not ok:
        await call.answer("Не удалось обновить", show_alert=True)
        return
    await audit_write(
        call.from_user.id, "shop.update", "shop", callback_data.shop_id,
        {
            "field": "monthly_weekday",
            "before": getattr(shop_before, "monthly_weekday", None),
            "after": callback_data.wd,
            "occurrence": callback_data.occurrence,
        },
    )
    await state.clear()
    today = date.today()
    info = resolve_cycle_info(
        None, None, callback_data.wd, today, monthly_occurrence=callback_data.occurrence,
    )
    upcoming = next_event_date(today, info, EventType.ARRIVAL)
    await call.message.answer(
        f"✅ Anchor изменён: завоз в {_OCCURRENCE_RU[callback_data.occurrence]} "
        f"{_WEEKDAY_RU[callback_data.wd]} каждого месяца "
        f"(ближайший — {upcoming.strftime('%d.%m.%Y')})"
    )
    shop = await get_shop(callback_data.shop_id)
    subs = await shop_subscribers_count(callback_data.shop_id)
    is_super = await is_super_admin(call.from_user.id)
    await call.message.answer(
        _format_card(shop, subs),
        reply_markup=_card_kb(callback_data.shop_id, callback_data.page, shop.is_active, is_super),
    )
    await call.answer()


@router.message(EditStates.value, F.text)
async def msg_edit_value(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    shop_id = data["shop_id"]
    field = data["field"]
    page = data.get("page", 0)
    if not await can_access_shop(message.from_user.id, shop_id):
        await state.clear()
        await message.answer("Нет доступа.")
        return
    raw = message.text.strip()
    label, max_len = FIELDS[field]
    if len(raw) > max_len:
        await message.answer(f"Слишком длинно (макс {max_len}). Введи короче.")
        return

    db_field = field
    value: object = raw
    if field == "working_hours":
        # «-» / «—» / пусто — это явная команда «очистить» (пишем NULL).
        value = None if raw in ("", "-", "—") else raw
    elif field == "chain_name":
        # Та же конвенция: «-» / «—» / пусто — сделать магазин независимым.
        value = None if raw in ("", "-", "—") else raw
    elif field == "cycle":
        try:
            n = int(raw)
            if n < 1 or n > 365:
                raise ValueError
        except ValueError:
            await message.answer("Цикл — целое число от 1 до 365.")
            return
        db_field = "cycle_length"
        value = n
    elif field == "anchor":
        try:
            value = datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            await message.answer("Формат даты YYYY-MM-DD.")
            return
        db_field = "anchor_date"
    elif field in ("price_start", "price_step"):
        try:
            n = int(raw)
            if n < 0 or n > 1_000_000:
                raise ValueError
        except ValueError:
            await message.answer("Введи целое неотрицательное число.")
            return
        value = n

    shop_before = await get_shop(shop_id)
    ok = await update_shop_field(shop_id, db_field, value)
    if not ok:
        await message.answer("Не удалось обновить.")
        await state.clear()
        return
    if field in ("cycle", "anchor") and shop_before and shop_before.monthly_weekday is not None:
        # Manually typing a cycle length/date means "go back to fixed mode" —
        # otherwise monthly_weekday would keep overriding what was just set.
        await update_shop_field(shop_id, "monthly_weekday", None)
    await audit_write(
        message.from_user.id, "shop.update", "shop", shop_id,
        {"field": db_field, "before": getattr(shop_before, db_field, None), "after": str(value)},
    )
    await state.clear()
    await message.answer(f"✅ «{label}» обновлено.")
    # show card again as a fresh message
    shop = await get_shop(shop_id)
    subs = await shop_subscribers_count(shop_id)
    is_super = await is_super_admin(message.from_user.id)
    await message.answer(_format_card(shop, subs), reply_markup=_card_kb(shop_id, page, shop.is_active, is_super))


# ---------- ACTIVATE / DEACTIVATE ----------

@router.callback_query(ShopCb.filter(F.action.in_({"deact", "act"})))
async def cb_toggle_active(call: CallbackQuery, callback_data: ShopCb) -> None:
    if not await can_access_shop(call.from_user.id, callback_data.shop_id):
        await audit_write(call.from_user.id, "access_denied", "shop", callback_data.shop_id)
        await call.answer("Нет доступа", show_alert=True)
        return
    new_state = callback_data.action == "act"
    ok = await update_shop_field(callback_data.shop_id, "is_active", new_state)
    if ok:
        await audit_write(
            call.from_user.id, "shop.is_active", "shop", callback_data.shop_id,
            {"value": new_state},
        )
    await _show_card(call, callback_data.shop_id, callback_data.page)


# ---------- DELETE (super only) ----------

@router.callback_query(ShopCb.filter(F.action == "delete"))
async def cb_delete_ask(call: CallbackQuery, callback_data: ShopCb) -> None:
    if not await is_super_admin(call.from_user.id):
        await call.answer("Только супер-админ", show_alert=True)
        return
    shop = await get_shop(callback_data.shop_id)
    if not shop:
        await call.answer("Не найден", show_alert=True)
        return
    subs = await shop_subscribers_count(callback_data.shop_id)
    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="🗑 Точно удалить",
            callback_data=ShopCb(action="deletec", shop_id=callback_data.shop_id, page=callback_data.page).pack(),
        ),
        InlineKeyboardButton(
            text="✖️ Отмена",
            callback_data=ShopCb(action="card", shop_id=callback_data.shop_id, page=callback_data.page).pack(),
        ),
    ]])
    warn = f"\n⚠️ Подписчиков: {subs} — они потеряют подписку." if subs else ""
    await safe_edit(
        call,
        f"🗑 Удалить «{html.escape(shop.name)}» (id={shop.id})?{warn}",
        confirm_kb,
    )
    await call.answer()


@router.callback_query(ShopCb.filter(F.action == "deletec"))
async def cb_delete_confirm(call: CallbackQuery, callback_data: ShopCb) -> None:
    if not await is_super_admin(call.from_user.id):
        await call.answer("Только супер-админ", show_alert=True)
        return
    from data.repos.shops import delete_shop
    ok = await delete_shop(callback_data.shop_id)
    if ok:
        await audit_write(call.from_user.id, "shop.delete", "shop", callback_data.shop_id)
        await safe_edit(call, "✅ Удалено", InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="← К списку", callback_data=ShopCb(action="list", page=callback_data.page).pack())
        ]]))
    else:
        await safe_edit(call, "Не нашёл", InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="← К списку", callback_data=ShopCb(action="list", page=callback_data.page).pack())
        ]]))
    await call.answer()
