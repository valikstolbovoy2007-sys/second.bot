import logging
from datetime import date, datetime, timedelta

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

from data.db import pool
from data.repos.admin_roles import (
    can_access_shop,
    is_super_admin,
    visible_shop_ids,
)
from data.repos.shops import get_shop, list_all_shops, list_shops_scoped, set_arrival, set_chain_arrival
from handlers.admin.filters import IsAdmin
from handlers.admin.ui import safe_edit
from services.audit import write as audit_write

log = logging.getLogger(__name__)
router = Router(name="admin_arrivals")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


class ArrCb(CallbackData, prefix="admarr"):
    action: str
    shop_id: int = 0
    page: int = 0
    value: str | None = None


class ArrStates(StatesGroup):
    date_input = State()
    chain_date_input = State()


def _menu_kb(is_super: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text="📅 Установить завоз для магазина",
            callback_data=ArrCb(action="pick").pack(),
        )],
        [InlineKeyboardButton(text="🗓 Календарь на месяц", callback_data=ArrCb(action="cal").pack())],
    ]
    if is_super:
        rows.append([InlineKeyboardButton(
            text="📅 Завоз для сети (super)",
            callback_data=ArrCb(action="chain").pack(),
        )])
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="adm:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------- CHAIN ARRIVAL (super only) ----------

@router.callback_query(ArrCb.filter(F.action == "chain"))
async def cb_chain_pick(call: CallbackQuery) -> None:
    if not await is_super_admin(call.from_user.id):
        await call.answer("Только супер-админ", show_alert=True)
        return
    shops = await list_all_shops()
    chains: dict[str, int] = {}
    for s in shops:
        if s.chain_name:
            chains[s.chain_name] = chains.get(s.chain_name, 0) + 1
    if not chains:
        await safe_edit(
            call, "Сетей нет.",
            InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="← Назад", callback_data="adm:arr:menu")]]),
        )
        await call.answer()
        return
    rows = [
        [InlineKeyboardButton(
            text=f"{name} ({n})",
            callback_data=ArrCb(action="cset", value=name).pack(),
        )]
        for name, n in sorted(chains.items())
    ]
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="adm:arr:menu")])
    await safe_edit(call, "Выбери сеть:", InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


def _chain_date_options(chain: str) -> InlineKeyboardMarkup:
    today = date.today()
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"Сегодня — {today.strftime('%d.%m')}",
            callback_data=ArrCb(action="ctoday", value=chain).pack(),
        )],
        [InlineKeyboardButton(
            text="Ввести вручную (YYYY-MM-DD)",
            callback_data=ArrCb(action="cmanual", value=chain).pack(),
        )],
        [InlineKeyboardButton(text="← Назад", callback_data=ArrCb(action="chain").pack())],
    ])


@router.callback_query(ArrCb.filter(F.action == "cset"))
async def cb_chain_set(call: CallbackQuery, callback_data: ArrCb) -> None:
    if not await is_super_admin(call.from_user.id):
        await call.answer("Только супер-админ", show_alert=True)
        return
    await safe_edit(
        call,
        f"Сеть: <b>{callback_data.value}</b>\n\nВыбери дату завоза:",
        _chain_date_options(callback_data.value),
    )
    await call.answer()


async def _apply_chain_arrival(actor_tg_id: int, chain: str, d: date) -> int:
    n = await set_chain_arrival(chain, d)
    await audit_write(
        actor_tg_id, "shop.set_chain_arrival", "chain", chain,
        {"date": d.isoformat(), "updated": n},
    )
    return n


@router.callback_query(ArrCb.filter(F.action == "ctoday"))
async def cb_chain_today(call: CallbackQuery, callback_data: ArrCb) -> None:
    if not await is_super_admin(call.from_user.id):
        await call.answer("Только супер-админ", show_alert=True)
        return
    n = await _apply_chain_arrival(call.from_user.id, callback_data.value, date.today())
    await safe_edit(
        call,
        f"✅ Сеть <b>{callback_data.value}</b>: anchor {date.today().strftime('%d.%m.%Y')}.\n"
        f"Обновлено магазинов: {n}",
        InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="← Назад", callback_data="adm:arr:menu")]]),
    )
    await call.answer()


@router.callback_query(ArrCb.filter(F.action == "cmanual"))
async def cb_chain_manual(call: CallbackQuery, callback_data: ArrCb, state: FSMContext) -> None:
    if not await is_super_admin(call.from_user.id):
        await call.answer("Только супер-админ", show_alert=True)
        return
    await state.set_state(ArrStates.chain_date_input)
    await state.update_data(chain=callback_data.value)
    await call.message.answer(
        f"Введи дату завоза для сети <b>{callback_data.value}</b> в формате YYYY-MM-DD:",
    )
    await call.answer()


@router.message(ArrStates.chain_date_input, F.text)
async def msg_chain_manual_date(message: Message, state: FSMContext) -> None:
    if not await is_super_admin(message.from_user.id):
        await state.clear()
        await message.answer("Нет доступа.")
        return
    data = await state.get_data()
    chain = data.get("chain")
    if not chain:
        await state.clear()
        await message.answer("Сессия истекла, попробуй ещё раз.")
        return
    try:
        d = datetime.strptime(message.text.strip(), "%Y-%m-%d").date()
    except ValueError:
        await message.answer("Формат YYYY-MM-DD, например 2026-05-15.")
        return
    n = await _apply_chain_arrival(message.from_user.id, chain, d)
    await state.clear()
    await message.answer(
        f"✅ Сеть <b>{chain}</b>: anchor {d.strftime('%d.%m.%Y')}.\n"
        f"Обновлено магазинов: {n}"
    )


@router.callback_query(F.data == "adm:arr:menu")
async def cb_menu(call: CallbackQuery) -> None:
    is_super = await is_super_admin(call.from_user.id)
    await safe_edit(call, "📦 <b>Завозы и циклы</b>\n\nВыбери действие:", _menu_kb(is_super))
    await call.answer()


@router.callback_query(ArrCb.filter(F.action == "pick"))
async def cb_pick(call: CallbackQuery) -> None:
    scope = await visible_shop_ids(call.from_user.id)
    items, total = await list_shops_scoped(scope, 50, 0)
    if total == 0:
        await safe_edit(
            call, "Нет доступных магазинов.",
            InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="← Назад", callback_data="adm:arr:menu")]]),
        )
        await call.answer()
        return
    rows = [[InlineKeyboardButton(
        text=f"{('['+s.chain_name+'] ') if s.chain_name else ''}{s.name}",
        callback_data=ArrCb(action="set", shop_id=s.id).pack(),
    )] for s in items]
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="adm:arr:menu")])
    await safe_edit(call, "Выбери магазин:", InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


def _date_options(shop_id: int) -> InlineKeyboardMarkup:
    today = date.today()
    rows = [
        [InlineKeyboardButton(
            text=f"Сегодня — {today.strftime('%d.%m')}",
            callback_data=ArrCb(action="today", shop_id=shop_id).pack(),
        )],
        [InlineKeyboardButton(
            text="Ввести вручную (YYYY-MM-DD)",
            callback_data=ArrCb(action="manual", shop_id=shop_id).pack(),
        )],
        [InlineKeyboardButton(text="← Назад", callback_data=ArrCb(action="pick").pack())],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(ArrCb.filter(F.action == "set"))
async def cb_set(call: CallbackQuery, callback_data: ArrCb) -> None:
    if not await can_access_shop(call.from_user.id, callback_data.shop_id):
        await audit_write(call.from_user.id, "access_denied", "shop", callback_data.shop_id)
        await call.answer("Нет доступа", show_alert=True)
        return
    shop = await get_shop(callback_data.shop_id)
    cur = shop.anchor_date.strftime("%d.%m.%Y") if shop and shop.anchor_date else "—"
    await safe_edit(call, f"Текущий anchor: {cur}\n\nВыбери новую дату:", _date_options(callback_data.shop_id))
    await call.answer()


async def _record_arrival(actor_tg_id: int, shop_id: int, d: date) -> None:
    await set_arrival(shop_id, d)
    async with pool().acquire() as conn:
        await conn.execute(
            "INSERT INTO arrivals (shop_id, arrival_date, source, set_by) VALUES ($1,$2,'manual',$3)",
            shop_id, d, actor_tg_id,
        )
    await audit_write(actor_tg_id, "shop.set_arrival", "shop", shop_id, {"date": d.isoformat()})


@router.callback_query(ArrCb.filter(F.action == "today"))
async def cb_today(call: CallbackQuery, callback_data: ArrCb) -> None:
    if not await can_access_shop(call.from_user.id, callback_data.shop_id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await _record_arrival(call.from_user.id, callback_data.shop_id, date.today())
    await safe_edit(
        call, f"✅ Anchor установлен: {date.today().strftime('%d.%m.%Y')}",
        InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="← Назад", callback_data="adm:arr:menu")]]),
    )
    await call.answer()


@router.callback_query(ArrCb.filter(F.action == "manual"))
async def cb_manual(call: CallbackQuery, callback_data: ArrCb, state: FSMContext) -> None:
    if not await can_access_shop(call.from_user.id, callback_data.shop_id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(ArrStates.date_input)
    await state.update_data(shop_id=callback_data.shop_id)
    await call.message.answer("Введи дату завоза в формате YYYY-MM-DD:")
    await call.answer()


@router.message(ArrStates.date_input, F.text)
async def msg_manual_date(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    shop_id = data["shop_id"]
    if not await can_access_shop(message.from_user.id, shop_id):
        await state.clear()
        await message.answer("Нет доступа.")
        return
    try:
        d = datetime.strptime(message.text.strip(), "%Y-%m-%d").date()
    except ValueError:
        await message.answer("Формат YYYY-MM-DD, например 2026-05-15.")
        return
    await _record_arrival(message.from_user.id, shop_id, d)
    await state.clear()
    await message.answer(f"✅ Anchor установлен: {d.strftime('%d.%m.%Y')}")


# ---------- History per shop (TZ §4.3) ----------

HIST_PAGE = 10


@router.callback_query(ArrCb.filter(F.action == "hist"))
async def cb_history(call: CallbackQuery, callback_data: ArrCb) -> None:
    if not await can_access_shop(call.from_user.id, callback_data.shop_id):
        await audit_write(call.from_user.id, "access_denied", "shop", callback_data.shop_id)
        await call.answer("Нет доступа", show_alert=True)
        return
    shop = await get_shop(callback_data.shop_id)
    if not shop:
        await call.answer("Магазин не найден", show_alert=True)
        return
    page = max(0, callback_data.page)
    async with pool().acquire() as conn:
        total = await conn.fetchval(
            "SELECT count(*) FROM arrivals WHERE shop_id=$1", callback_data.shop_id
        )
        rows = await conn.fetch(
            """
            SELECT arrival_date, source, set_by, set_at
            FROM arrivals
            WHERE shop_id=$1
            ORDER BY arrival_date DESC, id DESC
            LIMIT $2 OFFSET $3
            """,
            callback_data.shop_id, HIST_PAGE, page * HIST_PAGE,
        )
    pages = max(1, (total + HIST_PAGE - 1) // HIST_PAGE)
    if not rows:
        body = "История пуста."
    else:
        lines = []
        for r in rows:
            d = r["arrival_date"].strftime("%d.%m.%Y")
            src = r["source"]
            who = f" by {r['set_by']}" if r["set_by"] else ""
            ts = r["set_at"].strftime("%d.%m %H:%M")
            lines.append(f"• <b>{d}</b> — {src}{who} <i>({ts})</i>")
        body = "\n".join(lines)
    text = (
        f"📜 <b>История завозов</b>\n"
        f"🛍 {shop.name}\n"
        f"Всего записей: {total}\n\n{body}"
    )
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            text="◀️",
            callback_data=ArrCb(action="hist", shop_id=callback_data.shop_id, page=page - 1).pack(),
        ))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(
            text="▶️",
            callback_data=ArrCb(action="hist", shop_id=callback_data.shop_id, page=page + 1).pack(),
        ))
    kb_rows: list[list[InlineKeyboardButton]] = []
    if pages > 1:
        kb_rows.append(nav)
    from handlers.admin.shops import ShopCb
    kb_rows.append([InlineKeyboardButton(
        text="← К карточке",
        callback_data=ShopCb(action="card", shop_id=callback_data.shop_id, page=0).pack(),
    )])
    await safe_edit(call, text, InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await call.answer()


# ---------- Calendar (month-paginated, TZ §4.2) ----------

_RU_MONTHS = [
    "", "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
]


def _parse_month(value: str) -> date:
    if value:
        try:
            return datetime.strptime(value + "-01", "%Y-%m-%d").date()
        except ValueError:
            pass
    today = date.today()
    return date(today.year, today.month, 1)


def _add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, 1)


def _last_day(d: date) -> date:
    nxt = _add_months(d, 1)
    return nxt - timedelta(days=1)


@router.callback_query(ArrCb.filter(F.action == "cal"))
async def cb_calendar(call: CallbackQuery, callback_data: ArrCb) -> None:
    month_first = _parse_month(callback_data.value)
    month_last = _last_day(month_first)
    scope = await visible_shop_ids(call.from_user.id)
    items, _ = await list_shops_scoped(scope, 500, 0)
    days: dict[date, list] = {}
    for s in items:
        if not s.anchor_date or not s.cycle_length:
            continue
        d = s.anchor_date
        if d > month_last:
            delta = (d - month_first).days
            steps = (delta + s.cycle_length - 1) // s.cycle_length
            d = d - timedelta(days=steps * s.cycle_length)
        else:
            while d < month_first:
                d += timedelta(days=s.cycle_length)
        while d <= month_last:
            if d >= month_first:
                days.setdefault(d, []).append(s)
            d += timedelta(days=s.cycle_length)
    title = f"{_RU_MONTHS[month_first.month].capitalize()} {month_first.year}"
    if not days:
        body = "В этом месяце завозов не запланировано."
    else:
        lines = []
        for d in sorted(days):
            lines.append(f"<b>{d.strftime('%d.%m (%a)')}</b>")
            for s in days[d]:
                chain = f"[{s.chain_name}] " if s.chain_name else ""
                lines.append(f"  • {chain}{s.name}")
        body = "\n".join(lines)
    text = f"🗓 <b>{title}</b>\n\n{body}"
    if len(text) > 3800:
        text = text[:3800] + "\n…"
    prev_m = _add_months(month_first, -1)
    next_m = _add_months(month_first, +1)
    cur_m = date.today().replace(day=1)
    nav = [
        InlineKeyboardButton(
            text=f"◀️ {_RU_MONTHS[prev_m.month][:3]}",
            callback_data=ArrCb(action="cal", value=prev_m.strftime("%Y-%m")).pack(),
        ),
        InlineKeyboardButton(
            text="📅 Сегодня" if month_first != cur_m else "•сегодня•",
            callback_data=ArrCb(action="cal", value=cur_m.strftime("%Y-%m")).pack(),
        ),
        InlineKeyboardButton(
            text=f"{_RU_MONTHS[next_m.month][:3]} ▶️",
            callback_data=ArrCb(action="cal", value=next_m.strftime("%Y-%m")).pack(),
        ),
    ]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        nav,
        [InlineKeyboardButton(text="← Назад", callback_data="adm:arr:menu")],
    ])
    await safe_edit(call, text, kb)
    await call.answer()
