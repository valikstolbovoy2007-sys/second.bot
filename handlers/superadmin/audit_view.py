import html
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from handlers.admin.filters import IsSuperAdmin
from handlers.admin.ui import safe_edit
from services.audit import distinct_target_types, list_recent

log = logging.getLogger(__name__)
router = Router(name="sa_audit")
router.message.filter(IsSuperAdmin())
router.callback_query.filter(IsSuperAdmin())

PAGE_SIZE = 20

# Filters live in FSM data per super-admin session under key "audit_filter".
# Shape: {"actor": int|None, "action": str|None, "target_type": str|None,
#         "target_id": str|None, "days": int|None}


class AuditStates(StatesGroup):
    actor = State()
    action = State()
    target_id = State()


def _empty_filter() -> dict:
    return {"actor": None, "action": None, "target_type": None, "target_id": None, "days": None}


def _filter_label(f: dict) -> str:
    bits: list[str] = []
    if f.get("actor"):
        bits.append(f"actor={f['actor']}")
    if f.get("action"):
        bits.append(f"action~«{f['action']}»")
    if f.get("target_type"):
        bits.append(f"type={f['target_type']}")
    if f.get("target_id"):
        bits.append(f"id={f['target_id']}")
    if f.get("days"):
        bits.append(f"≤{f['days']}д")
    return ", ".join(bits) if bits else "без фильтров"


async def _get_filter(state: FSMContext) -> dict:
    data = await state.get_data()
    return dict(data.get("audit_filter") or _empty_filter())


async def _set_filter(state: FSMContext, f: dict) -> None:
    await state.update_data(audit_filter=f)


def _filters_kb(f: dict) -> InlineKeyboardMarkup:
    days = f.get("days")
    rows = [
        [InlineKeyboardButton(text="🔄 Применить / показать", callback_data="sa:audit:p:0")],
        [
            InlineKeyboardButton(text=("📅 24ч ✓" if days == 1 else "📅 24ч"),
                                 callback_data="sa:audit:f:days:1"),
            InlineKeyboardButton(text=("📅 7д ✓" if days == 7 else "📅 7д"),
                                 callback_data="sa:audit:f:days:7"),
            InlineKeyboardButton(text=("📅 30д ✓" if days == 30 else "📅 30д"),
                                 callback_data="sa:audit:f:days:30"),
            InlineKeyboardButton(text=("📅 всё ✓" if not days else "📅 всё"),
                                 callback_data="sa:audit:f:days:0"),
        ],
        [InlineKeyboardButton(text="👤 Actor", callback_data="sa:audit:f:actor"),
         InlineKeyboardButton(text="🎬 Action", callback_data="sa:audit:f:action")],
        [InlineKeyboardButton(text="🏷 Target type", callback_data="sa:audit:f:ttype"),
         InlineKeyboardButton(text="🆔 Target id", callback_data="sa:audit:f:tid")],
        [InlineKeyboardButton(text="🧹 Сбросить", callback_data="sa:audit:f:reset")],
        [InlineKeyboardButton(text="← Назад", callback_data="sa:menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "sa:audit:filters")
async def cb_filters(call: CallbackQuery, state: FSMContext) -> None:
    f = await _get_filter(state)
    await safe_edit(
        call,
        f"⚙️ <b>Фильтры аудита</b>\n\nТекущие: {_filter_label(f)}",
        _filters_kb(f),
    )
    await call.answer()


@router.callback_query(F.data.startswith("sa:audit:f:days:"))
async def cb_filter_days(call: CallbackQuery, state: FSMContext) -> None:
    try:
        d = int(call.data.rsplit(":", 1)[-1])
    except ValueError:
        await call.answer("Битый callback", show_alert=True)
        return
    f = await _get_filter(state)
    f["days"] = d if d > 0 else None
    await _set_filter(state, f)
    await cb_filters(call, state)


@router.callback_query(F.data == "sa:audit:f:reset")
async def cb_filter_reset(call: CallbackQuery, state: FSMContext) -> None:
    await _set_filter(state, _empty_filter())
    await cb_filters(call, state)


@router.callback_query(F.data == "sa:audit:f:actor")
async def cb_filter_actor_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AuditStates.actor)
    await call.message.answer(
        "Введи tg_id актора (число), или /clear чтобы очистить, /cancel — отмена:",
    )
    await call.answer()


@router.message(AuditStates.actor, F.text)
async def msg_filter_actor(message: Message, state: FSMContext) -> None:
    raw = message.text.strip()
    f = await _get_filter(state)
    if raw == "/cancel":
        await state.set_state(None)
        await message.answer("Отменено.")
        return
    if raw == "/clear":
        f["actor"] = None
    else:
        try:
            f["actor"] = int(raw.lstrip("@"))
        except ValueError:
            await message.answer("Это не число. Повтори.")
            return
    await _set_filter(state, f)
    await state.set_state(None)
    await message.answer(f"✅ Фильтр actor: {f['actor'] or '—'}")


@router.callback_query(F.data == "sa:audit:f:action")
async def cb_filter_action_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AuditStates.action)
    await call.message.answer(
        "Введи подстроку action (например, 'shop' или 'broadcast'),\n"
        "/clear — очистить, /cancel — отмена:",
    )
    await call.answer()


@router.message(AuditStates.action, F.text)
async def msg_filter_action(message: Message, state: FSMContext) -> None:
    raw = message.text.strip()
    f = await _get_filter(state)
    if raw == "/cancel":
        await state.set_state(None)
        await message.answer("Отменено.")
        return
    if raw == "/clear":
        f["action"] = None
    else:
        if len(raw) > 100:
            await message.answer("До 100 символов.")
            return
        f["action"] = raw
    await _set_filter(state, f)
    await state.set_state(None)
    await message.answer(f"✅ Фильтр action: «{f['action'] or '—'}»")


@router.callback_query(F.data == "sa:audit:f:ttype")
async def cb_filter_ttype(call: CallbackQuery, state: FSMContext) -> None:
    f = await _get_filter(state)
    types = await distinct_target_types(20)
    rows: list[list[InlineKeyboardButton]] = []
    for t in types:
        mark = " ✓" if f.get("target_type") == t else ""
        rows.append([InlineKeyboardButton(
            text=f"{t}{mark}", callback_data=f"sa:audit:f:ttype:{t}",
        )])
    rows.append([InlineKeyboardButton(text="🧹 Сбросить", callback_data="sa:audit:f:ttype:")])
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="sa:audit:filters")])
    await safe_edit(call, "🏷 Выбери target_type:", InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


@router.callback_query(F.data.startswith("sa:audit:f:ttype:"))
async def cb_filter_ttype_set(call: CallbackQuery, state: FSMContext) -> None:
    val = call.data[len("sa:audit:f:ttype:"):]
    f = await _get_filter(state)
    f["target_type"] = val or None
    await _set_filter(state, f)
    await cb_filters(call, state)


@router.callback_query(F.data == "sa:audit:f:tid")
async def cb_filter_tid_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AuditStates.target_id)
    await call.message.answer("Введи target_id (строкой), /clear — очистить, /cancel — отмена:")
    await call.answer()


@router.message(AuditStates.target_id, F.text)
async def msg_filter_tid(message: Message, state: FSMContext) -> None:
    raw = message.text.strip()
    f = await _get_filter(state)
    if raw == "/cancel":
        await state.set_state(None)
        await message.answer("Отменено.")
        return
    if raw == "/clear":
        f["target_id"] = None
    else:
        if len(raw) > 100:
            await message.answer("До 100 символов.")
            return
        f["target_id"] = raw
    await _set_filter(state, f)
    await state.set_state(None)
    await message.answer(f"✅ Фильтр target_id: «{f['target_id'] or '—'}»")


@router.callback_query(F.data.startswith("sa:audit:p:"))
async def cb_audit(call: CallbackQuery, state: FSMContext) -> None:
    try:
        page = int(call.data.rsplit(":", 1)[-1])
    except ValueError:
        await call.answer("Битый callback", show_alert=True)
        return
    f = await _get_filter(state)
    rows = await list_recent(
        PAGE_SIZE, page * PAGE_SIZE,
        actor_tg_id=f.get("actor"),
        action_like=f.get("action"),
        target_type=f.get("target_type"),
        target_id=f.get("target_id"),
        days=f.get("days"),
    )
    nav: list[InlineKeyboardButton] = []
    kb_rows: list[list[InlineKeyboardButton]] = []
    if not rows:
        await safe_edit(
            call,
            f"📜 <b>Аудит</b>\nФильтры: {_filter_label(f)}\n\nНичего не найдено.",
            InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⚙️ Фильтры", callback_data="sa:audit:filters")],
                [InlineKeyboardButton(text="← Назад", callback_data="sa:menu")],
            ]),
        )
        await call.answer()
        return
    lines = [
        f"📜 <b>Аудит</b> (стр. {page + 1})",
        f"<i>Фильтры:</i> {_filter_label(f)}",
        "",
    ]
    for r in rows:
        ts = r["ts"].strftime("%d.%m %H:%M")
        target = f"{r['target_type']}#{r['target_id']}" if r['target_type'] else "—"
        lines.append(
            f"<code>{ts}</code> {r['actor_tg_id'] or '—'} • {html.escape(r['action'])} • {target}"
        )
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"sa:audit:p:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"стр {page + 1}", callback_data="noop"))
    if len(rows) == PAGE_SIZE:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"sa:audit:p:{page + 1}"))
    kb_rows.append(nav)
    kb_rows.append([InlineKeyboardButton(text="⚙️ Фильтры", callback_data="sa:audit:filters")])
    kb_rows.append([InlineKeyboardButton(text="← Назад", callback_data="sa:menu")])
    await safe_edit(call, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await call.answer()
