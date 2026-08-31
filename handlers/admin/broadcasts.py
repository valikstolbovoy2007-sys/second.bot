"""Broadcasts UI for admins (TZ §6).

Walks the operator through:
  1) audience kind (all / chain / shop subscribers / active recent)
  2) text + optional photo/document
  3) optional schedule
  4) confirmation -> enqueue. Dispatch is handled by services/broadcasts.

Also exposes a history view with pause / resume / cancel actions.
"""
import html
import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from data.repos.admin_roles import is_super_admin, visible_shop_ids
from data.repos.chains import list_chains
from data.repos.shops import list_shops_scoped
from handlers.admin.filters import IsSuperAdmin
from handlers.admin.ui import safe_edit
from services.audit import write as audit_write
from services.broadcasts import (
    count_recent_by_actor,
    enqueue,
    get,
    list_recent,
    request_cancel,
    request_pause,
    request_resume,
    resolve_audience,
)
from states.admin_states import BroadcastStates

log = logging.getLogger(__name__)
router = Router(name="admin_broadcasts")
router.message.filter(IsSuperAdmin())
router.callback_query.filter(IsSuperAdmin())

TZ = ZoneInfo("Europe/Moscow")


class BcCb(CallbackData, prefix="admbc"):
    action: str
    value: str | None = None


# ---------- menu ----------

def _menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🆕 Новая рассылка", callback_data=BcCb(action="new").pack())],
        [InlineKeyboardButton(text="📜 История", callback_data=BcCb(action="hist", value="0").pack())],
        [InlineKeyboardButton(text="← Назад", callback_data="adm:back")],
    ])


@router.callback_query(F.data == "adm:bc:menu")
async def cb_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await safe_edit(call, "📣 <b>Рассылки</b>", _menu_kb())
    await call.answer()


# ---------- audience picker ----------

async def _audience_kb(actor_tg_id: int) -> InlineKeyboardMarkup:
    is_super = await is_super_admin(actor_tg_id)
    rows: list[list[InlineKeyboardButton]] = []
    if is_super:
        rows.append([InlineKeyboardButton(text="📣 Всем активным",
                                          callback_data=BcCb(action="aud", value="all").pack())])
        rows.append([InlineKeyboardButton(text="🆕 Зарегистрировавшимся за 30 дней",
                                          callback_data=BcCb(action="aud", value="recent30").pack())])
    rows.append([InlineKeyboardButton(text="🛍 Подписчикам моих магазинов",
                                      callback_data=BcCb(action="aud", value="my").pack())])
    rows.append([InlineKeyboardButton(text="🔗 Подписчикам сети",
                                      callback_data=BcCb(action="aud", value="chain").pack())])
    rows.append([InlineKeyboardButton(text="🎯 Подписчикам конкретных магазинов",
                                      callback_data=BcCb(action="aud", value="shops").pack())])
    if is_super:
        rows.append([InlineKeyboardButton(text="📋 По списку tg_id (файл/текст)",
                                          callback_data=BcCb(action="aud", value="list").pack())])
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="adm:bc:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(BcCb.filter(F.action == "new"))
async def cb_new(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(BroadcastStates.audience)
    kb = await _audience_kb(call.from_user.id)
    await safe_edit(call, "Кому отправляем?", kb)
    await call.answer()


@router.callback_query(BroadcastStates.audience, BcCb.filter(F.action == "aud"))
async def cb_audience(call: CallbackQuery, callback_data: BcCb, state: FSMContext) -> None:
    kind = callback_data.value
    if kind == "all":
        if not await is_super_admin(call.from_user.id):
            await call.answer("Только супер-админ", show_alert=True)
            return
        await state.update_data(audience={"kind": "all"})
        await _go_text(call, state)
    elif kind == "recent30":
        if not await is_super_admin(call.from_user.id):
            await call.answer("Только супер-админ", show_alert=True)
            return
        await state.update_data(audience={"kind": "active_recent", "days": 30})
        await _go_text(call, state)
    elif kind == "my":
        scope = await visible_shop_ids(call.from_user.id)
        if scope is None:
            # super: equal to subscribers-of-all-shops i.e. "all" subscribers
            await state.update_data(audience={"kind": "all"})
        else:
            if not scope:
                await call.answer("За тобой не закреплён ни один магазин", show_alert=True)
                return
            await state.update_data(audience={"kind": "subscribers", "shop_ids": list(scope)})
        await _go_text(call, state)
    elif kind == "chain":
        chains = await list_chains(only_active=True)
        if not chains:
            await call.answer("Нет активных сетей", show_alert=True)
            return
        rows = [[InlineKeyboardButton(text=c.name, callback_data=BcCb(action="chain", value=c.name).pack())]
                for c in chains[:30]]
        rows.append([InlineKeyboardButton(text="← Назад", callback_data=BcCb(action="new").pack())])
        await safe_edit(call, "Выбери сеть:", InlineKeyboardMarkup(inline_keyboard=rows))
    elif kind == "shops":
        scope = await visible_shop_ids(call.from_user.id)
        shops, _ = await list_shops_scoped(scope, limit=30, offset=0)
        if not shops:
            await call.answer("Нет доступных магазинов", show_alert=True)
            return
        await state.update_data(shop_ids=[])
        await _render_shop_picker(call, state, shops)
    elif kind == "list":
        if not await is_super_admin(call.from_user.id):
            await call.answer("Только супер-админ", show_alert=True)
            return
        await state.set_state(BroadcastStates.audience_list_upload)
        await call.message.answer(
            "📋 Пришли файл (.txt/.csv) со списком tg_id — по одному на строку,\n"
            "или просто список через запятую/пробел/перенос строки в сообщении.\n"
            "Отмена: /cancel",
        )
    await call.answer()


def _parse_tg_ids(raw: str) -> tuple[list[int], int]:
    """Parse tg_ids from raw text, return (ids, ignored_lines)."""
    seen: set[int] = set()
    ordered: list[int] = []
    bad = 0
    for token in raw.replace(",", " ").replace(";", " ").split():
        token = token.strip().lstrip("@")
        if not token:
            continue
        try:
            n = int(token)
        except ValueError:
            bad += 1
            continue
        if n in seen:
            continue
        seen.add(n)
        ordered.append(n)
    return ordered, bad


async def _accept_tg_ids(message: Message, state: FSMContext, raw: str) -> None:
    ids, bad = _parse_tg_ids(raw)
    if not ids:
        await message.answer("Не нашёл ни одного валидного tg_id. Повтори.")
        return
    await state.update_data(audience={"kind": "list", "tg_ids": ids})
    skip_label = f", пропущено невалидных: {bad}" if bad else ""
    await message.answer(f"✅ Получено {len(ids)} tg_id{skip_label}.")
    await _go_text_msg(message, state)


async def _go_text_msg(message: Message, state: FSMContext) -> None:
    await state.set_state(BroadcastStates.text)
    await message.answer(
        "Введи текст рассылки. Поддерживается HTML.\n"
        "Чтобы прикрепить картинку — пришли её следующим сообщением (или нажми «Без медиа»).\n"
        "Отмена: /cancel",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✖️ Отмена", callback_data="adm:bc:menu"),
        ]]),
    )


@router.message(BroadcastStates.audience_list_upload, F.text == "/cancel")
async def msg_list_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено.")


@router.message(BroadcastStates.audience_list_upload, F.document)
async def msg_list_doc(message: Message, state: FSMContext) -> None:
    if not await is_super_admin(message.from_user.id):
        await state.clear()
        await message.answer("Нет доступа.")
        return
    doc = message.document
    if doc.file_size and doc.file_size > 2 * 1024 * 1024:
        await message.answer("Файл слишком большой (>2 МБ).")
        return
    try:
        buf = await message.bot.download(doc)
        raw = buf.read().decode("utf-8", errors="replace")
    except Exception:
        log.exception("download tg_id list failed")
        await message.answer("Не смог скачать файл, повтори.")
        return
    await _accept_tg_ids(message, state, raw)


@router.message(BroadcastStates.audience_list_upload, F.text)
async def msg_list_text(message: Message, state: FSMContext) -> None:
    if not await is_super_admin(message.from_user.id):
        await state.clear()
        await message.answer("Нет доступа.")
        return
    await _accept_tg_ids(message, state, message.text)


@router.callback_query(BroadcastStates.audience, BcCb.filter(F.action == "chain"))
async def cb_chain_pick(call: CallbackQuery, callback_data: BcCb, state: FSMContext) -> None:
    await state.update_data(audience={"kind": "chain", "chain": callback_data.value})
    await _go_text(call, state)
    await call.answer()


async def _render_shop_picker(call, state: FSMContext, shops) -> None:
    data = await state.get_data()
    selected: list[int] = list(data.get("shop_ids") or [])
    rows = []
    for s in shops:
        marker = "✅ " if s.id in selected else ""
        rows.append([InlineKeyboardButton(
            text=f"{marker}{s.name}",
            callback_data=BcCb(action="tog", value=str(s.id)).pack(),
        )])
    rows.append([InlineKeyboardButton(text=f"➡️ Дальше (выбрано {len(selected)})",
                                      callback_data=BcCb(action="shopsdone").pack())])
    rows.append([InlineKeyboardButton(text="← Назад", callback_data=BcCb(action="new").pack())])
    await safe_edit(call, "Отметь магазины (тап = переключить):",
                    InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(BroadcastStates.audience, BcCb.filter(F.action == "tog"))
async def cb_shop_toggle(call: CallbackQuery, callback_data: BcCb, state: FSMContext) -> None:
    try:
        shop_id = int(callback_data.value)
    except ValueError:
        await call.answer("Битый ввод", show_alert=True)
        return
    data = await state.get_data()
    selected: list[int] = list(data.get("shop_ids") or [])
    if shop_id in selected:
        selected.remove(shop_id)
    else:
        selected.append(shop_id)
    await state.update_data(shop_ids=selected)
    scope = await visible_shop_ids(call.from_user.id)
    shops = await list_shops_scoped(scope, limit=30, offset=0, search=None, active_only=True)
    await _render_shop_picker(call, state, shops)
    await call.answer()


@router.callback_query(BroadcastStates.audience, BcCb.filter(F.action == "shopsdone"))
async def cb_shops_done(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    shop_ids: list[int] = list(data.get("shop_ids") or [])
    if not shop_ids:
        await call.answer("Не выбрано ни одного магазина", show_alert=True)
        return
    await state.update_data(audience={"kind": "subscribers", "shop_ids": shop_ids})
    await _go_text(call, state)
    await call.answer()


# ---------- text / media ----------

async def _go_text(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(BroadcastStates.text)
    await safe_edit(
        call,
        "Введи текст рассылки. Поддерживается HTML.\n"
        "Чтобы прикрепить картинку — пришли её следующим сообщением (или нажми «Без медиа»).\n"
        "Отмена: /cancel",
        InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✖️ Отмена", callback_data="adm:bc:menu"),
        ]]),
    )


@router.message(BroadcastStates.text, F.text == "/cancel")
async def msg_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено.")


@router.message(BroadcastStates.text, F.text)
async def msg_text(message: Message, state: FSMContext) -> None:
    text = message.html_text
    await state.update_data(text=text, photo=None, document=None)
    await state.set_state(BroadcastStates.media)
    await message.answer(
        "📷 Пришли картинку или документ для прикрепления, либо «Без медиа».",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚫 Без медиа", callback_data=BcCb(action="nomedia").pack())],
            [InlineKeyboardButton(text="✖️ Отмена", callback_data="adm:bc:menu")],
        ]),
    )


@router.message(BroadcastStates.media, F.photo)
async def msg_photo(message: Message, state: FSMContext) -> None:
    file_id = message.photo[-1].file_id
    await state.update_data(photo=file_id, document=None)
    await _go_schedule(message, state)


@router.message(BroadcastStates.media, F.document)
async def msg_doc(message: Message, state: FSMContext) -> None:
    await state.update_data(document=message.document.file_id, photo=None)
    await _go_schedule(message, state)


@router.callback_query(BroadcastStates.media, BcCb.filter(F.action == "nomedia"))
async def cb_no_media(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(photo=None, document=None)
    await _go_schedule(call.message, state)
    await call.answer()


# ---------- schedule ----------

async def _go_schedule(message_like, state: FSMContext) -> None:
    await state.set_state(BroadcastStates.schedule)
    rows = [
        [InlineKeyboardButton(text="🚀 Сейчас", callback_data=BcCb(action="sched", value="now").pack())],
        [InlineKeyboardButton(text="🕒 +1 час", callback_data=BcCb(action="sched", value="+60").pack())],
        [InlineKeyboardButton(text="🕒 +6 часов", callback_data=BcCb(action="sched", value="+360").pack())],
        [InlineKeyboardButton(text="📅 Указать вручную (YYYY-MM-DD HH:MM)",
                              callback_data=BcCb(action="sched", value="manual").pack())],
        [InlineKeyboardButton(text="✖️ Отмена", callback_data="adm:bc:menu")],
    ]
    if hasattr(message_like, "answer"):
        await message_like.answer("Когда отправить?", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(BroadcastStates.schedule, BcCb.filter(F.action == "sched"))
async def cb_schedule(call: CallbackQuery, callback_data: BcCb, state: FSMContext) -> None:
    val = callback_data.value
    if val == "now":
        await state.update_data(scheduled_at=None)
        await _go_confirm(call, state)
    elif val == "manual":
        await call.message.answer(
            "Введи дату и время в формате <code>YYYY-MM-DD HH:MM</code> по Москве:",
        )
    elif val.startswith("+"):
        try:
            mins = int(val[1:])
        except ValueError:
            await call.answer("Битый ввод", show_alert=True)
            return
        when = datetime.now(TZ) + timedelta(minutes=mins)
        await state.update_data(scheduled_at=when.isoformat())
        await _go_confirm(call, state)
    await call.answer()


@router.message(BroadcastStates.schedule, F.text)
async def msg_schedule_manual(message: Message, state: FSMContext) -> None:
    raw = message.text.strip()
    if raw == "/cancel":
        await state.clear()
        await message.answer("Отменено.")
        return
    try:
        when = datetime.strptime(raw, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
    except ValueError:
        await message.answer("Формат YYYY-MM-DD HH:MM. Повтори.")
        return
    if when <= datetime.now(TZ):
        await message.answer("Время в прошлом. Введи будущее.")
        return
    await state.update_data(scheduled_at=when.isoformat())
    await _go_confirm_msg(message, state)


# ---------- confirm ----------

def _format_audience(audience: dict) -> str:
    kind = audience.get("kind")
    if kind == "all":
        return "Все активные"
    if kind == "active_recent":
        return f"Активные за {audience.get('days', 30)} дн."
    if kind == "subscribers":
        return f"Подписчики магазинов ({len(audience.get('shop_ids', []))} шт.)"
    if kind == "chain":
        return f"Подписчики сети «{audience.get('chain')}»"
    if kind == "list":
        return f"По списку tg_id ({len(audience.get('tg_ids', []))} шт.)"
    return kind or "?"


async def _build_preview(state: FSMContext, actor_tg_id: int) -> tuple[str, dict]:
    data = await state.get_data()
    audience = data.get("audience") or {"kind": "all"}
    text: str = data.get("text", "")
    photo = data.get("photo")
    document = data.get("document")
    sched = data.get("scheduled_at")
    sched_label = "сейчас" if not sched else sched.replace("T", " ")[:16]
    media_label = "—"
    if photo:
        media_label = "📷 фото"
    elif document:
        media_label = "📎 документ"
    tg_ids = await resolve_audience(actor_tg_id, audience)
    preview = (
        "<b>Превью рассылки</b>\n\n"
        f"{text}\n\n"
        f"— Аудитория: {_format_audience(audience)} (≈{len(tg_ids)})\n"
        f"— Медиа: {media_label}\n"
        f"— Время: {sched_label}"
    )
    return preview, {
        "audience": audience,
        "payload": {"text": text, "photo": photo, "document": document},
        "scheduled_at": sched,
        "audience_size": len(tg_ids),
    }


async def _go_confirm(call: CallbackQuery, state: FSMContext) -> None:
    text, _ = await _build_preview(state, call.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Поставить в очередь",
                             callback_data=BcCb(action="enqueue").pack()),
        InlineKeyboardButton(text="✖️ Отмена",
                             callback_data="adm:bc:menu"),
    ]])
    await state.set_state(BroadcastStates.confirm)
    await safe_edit(call, text, kb)


async def _go_confirm_msg(message: Message, state: FSMContext) -> None:
    text, _ = await _build_preview(state, message.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Поставить в очередь",
                             callback_data=BcCb(action="enqueue").pack()),
        InlineKeyboardButton(text="✖️ Отмена",
                             callback_data="adm:bc:menu"),
    ]])
    await state.set_state(BroadcastStates.confirm)
    await message.answer(text, reply_markup=kb)


@router.callback_query(BroadcastStates.confirm, BcCb.filter(F.action == "enqueue"))
async def cb_enqueue(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("freq_warned"):
        recent = await count_recent_by_actor(call.from_user.id, hours=24)
        if recent > 2:
            await state.update_data(freq_warned=True)
            text, _ = await _build_preview(state, call.from_user.id)
            warn = (
                f"⚠️ За последние 24 ч ты уже создал {recent} рассылок.\n"
                "Точно отправить ещё одну?"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text="✅ Да, отправить",
                    callback_data=BcCb(action="enqueue").pack(),
                ),
                InlineKeyboardButton(text="✖️ Отмена", callback_data="adm:bc:menu"),
            ]])
            await safe_edit(call, f"{text}\n\n{warn}", kb)
            await call.answer()
            return
    _, info = await _build_preview(state, call.from_user.id)
    sched_str = info["scheduled_at"]
    sched_dt = None
    if sched_str:
        try:
            sched_dt = datetime.fromisoformat(sched_str)
        except ValueError:
            sched_dt = None
    bc_id = await enqueue(
        payload=info["payload"],
        audience_filter=info["audience"],
        created_by=call.from_user.id,
        scheduled_at=sched_dt,
    )
    await audit_write(
        call.from_user.id, "broadcast.enqueue", "broadcast", bc_id,
        {"audience": info["audience"], "scheduled_at": sched_str,
         "audience_size": info["audience_size"]},
    )
    await state.clear()
    label = "Запущена" if not sched_dt else f"Запланирована на {sched_str}"
    await safe_edit(
        call,
        f"✅ Рассылка #{bc_id} {label.lower()} (≈{info['audience_size']} получателей).",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📜 К истории", callback_data=BcCb(action="hist", value="0").pack())],
            [InlineKeyboardButton(text="← В меню", callback_data="adm:bc:menu")],
        ]),
    )
    await call.answer("Поставлено в очередь")


# ---------- history ----------

PAGE = 10


@router.callback_query(BcCb.filter(F.action == "hist"))
async def cb_hist(call: CallbackQuery, callback_data: BcCb) -> None:
    try:
        page = int(callback_data.value or "0")
    except ValueError:
        page = 0
    items = await list_recent(PAGE, page * PAGE)
    if not items:
        await safe_edit(
            call, "История пуста.",
            InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="← Назад", callback_data="adm:bc:menu")]]),
        )
        await call.answer()
        return
    lines = [f"📜 <b>История рассылок</b> (стр. {page + 1})", ""]
    rows: list[list[InlineKeyboardButton]] = []
    for bc in items:
        kind = bc.audience_filter.get("kind", "?")
        snippet = (bc.payload.get("text") or "").replace("\n", " ")[:40]
        lines.append(f"#{bc.id} • {bc.status} • {kind} • {bc.sent}/{bc.failed} • «{html.escape(snippet)}»")
        rows.append([InlineKeyboardButton(
            text=f"#{bc.id} {bc.status}",
            callback_data=BcCb(action="view", value=str(bc.id)).pack(),
        )])
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=BcCb(action="hist", value=str(page - 1)).pack()))
    nav.append(InlineKeyboardButton(text=f"стр {page + 1}", callback_data="noop"))
    if len(items) == PAGE:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=BcCb(action="hist", value=str(page + 1)).pack()))
    rows.append(nav)
    rows.append([InlineKeyboardButton(text="← В меню", callback_data="adm:bc:menu")])
    await safe_edit(call, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


@router.callback_query(BcCb.filter(F.action == "view"))
async def cb_view(call: CallbackQuery, callback_data: BcCb) -> None:
    try:
        bc_id = int(callback_data.value)
    except ValueError:
        await call.answer("Битый id", show_alert=True)
        return
    bc = await get(bc_id)
    if not bc:
        await call.answer("Не найдено", show_alert=True)
        return
    text = (
        f"📣 <b>Broadcast #{bc.id}</b>\n"
        f"Статус: <b>{bc.status}</b>\n"
        f"Отправлено: {bc.sent}, ошибок: {bc.failed}\n"
        f"Аудитория: <code>{html.escape(json.dumps(bc.audience_filter, ensure_ascii=False))}</code>\n"
        f"Расписание: {bc.scheduled_at or '—'}\n\n"
        f"<b>Текст:</b>\n{(bc.payload.get('text') or '')[:2000]}"
    )
    rows: list[list[InlineKeyboardButton]] = []
    if bc.status == "running":
        rows.append([InlineKeyboardButton(text="⏸ Пауза",
                                          callback_data=BcCb(action="pause", value=str(bc.id)).pack())])
        rows.append([InlineKeyboardButton(text="🛑 Отмена",
                                          callback_data=BcCb(action="cancel", value=str(bc.id)).pack())])
    elif bc.status == "paused":
        rows.append([InlineKeyboardButton(text="▶️ Возобновить",
                                          callback_data=BcCb(action="resume", value=str(bc.id)).pack())])
        rows.append([InlineKeyboardButton(text="🛑 Отмена",
                                          callback_data=BcCb(action="cancel", value=str(bc.id)).pack())])
    elif bc.status == "pending":
        rows.append([InlineKeyboardButton(text="🛑 Отмена",
                                          callback_data=BcCb(action="cancel", value=str(bc.id)).pack())])
    rows.append([InlineKeyboardButton(text="← К списку", callback_data=BcCb(action="hist", value="0").pack())])
    await safe_edit(call, text, InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


@router.callback_query(BcCb.filter(F.action == "pause"))
async def cb_pause(call: CallbackQuery, callback_data: BcCb) -> None:
    try:
        bc_id = int(callback_data.value)
    except ValueError:
        await call.answer("Битый id", show_alert=True)
        return
    await request_pause(bc_id)
    await audit_write(call.from_user.id, "broadcast.pause", "broadcast", bc_id, None)
    await call.answer("Поставлено на паузу")
    callback_data_view = BcCb(action="view", value=str(bc_id))
    call.data = callback_data_view.pack()
    await cb_view(call, callback_data_view)


@router.callback_query(BcCb.filter(F.action == "resume"))
async def cb_resume(call: CallbackQuery, callback_data: BcCb) -> None:
    try:
        bc_id = int(callback_data.value)
    except ValueError:
        await call.answer("Битый id", show_alert=True)
        return
    await request_resume(bc_id)
    await audit_write(call.from_user.id, "broadcast.resume", "broadcast", bc_id, None)
    await call.answer("Возобновлено")
    callback_data_view = BcCb(action="view", value=str(bc_id))
    call.data = callback_data_view.pack()
    await cb_view(call, callback_data_view)


@router.callback_query(BcCb.filter(F.action == "cancel"))
async def cb_cancel(call: CallbackQuery, callback_data: BcCb) -> None:
    try:
        bc_id = int(callback_data.value)
    except ValueError:
        await call.answer("Битый id", show_alert=True)
        return
    await request_cancel(bc_id)
    await audit_write(call.from_user.id, "broadcast.cancel", "broadcast", bc_id, None)
    await call.answer("Отменено")
    callback_data_view = BcCb(action="view", value=str(bc_id))
    call.data = callback_data_view.pack()
    await cb_view(call, callback_data_view)
