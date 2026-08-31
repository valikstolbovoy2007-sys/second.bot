"""Parsers admin UI (TZ §8).

Lists registered parsers with status (enabled, cron, last run). Lets admins
trigger a run manually, or do a dry-run. Super admins can edit cron expression
and enabled flag through `bot_config`.
"""
import html
import logging

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
from handlers.admin.filters import IsAdmin
from handlers.admin.ui import safe_edit
from services.audit import write as audit_write
from services.config_live import get as cfg_get, set_value as cfg_set
from services.parsers import (
    REGISTRY,
    get_cron,
    is_enabled,
    list_runs,
    resolve_actor_scope,
    run_parser,
)
from states.admin_states import ParserScheduleStates

log = logging.getLogger(__name__)
router = Router(name="admin_parsers")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


class ParCb(CallbackData, prefix="admpar"):
    action: str
    key: str | None = None


# ---------- menu ----------

@router.callback_query(F.data == "adm:par:menu")
async def cb_menu(call: CallbackQuery) -> None:
    rows: list[list[InlineKeyboardButton]] = []
    for key, p in REGISTRY.items():
        enabled = await is_enabled(key)
        marker = "🟢" if enabled else "⚪️"
        rows.append([InlineKeyboardButton(
            text=f"{marker} {p.label}",
            callback_data=ParCb(action="view", key=key).pack(),
        )])
    rows.append([InlineKeyboardButton(text="📜 Лог запусков", callback_data=ParCb(action="log").pack())])
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="adm:back")])
    await safe_edit(call, "🚚 <b>Парсеры</b>\n\nВыбери источник:",
                    InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


# ---------- legacy direct trigger (preserve old button) ----------

@router.callback_query(F.data == "adm:par:megahand")
async def cb_megahand_direct(call: CallbackQuery) -> None:
    await _do_run(call, "megahand", apply=True, show_progress_text=True)


# ---------- per-parser view ----------

async def _last_run_summary(key: str) -> str:
    rows = await list_runs(key, limit=1)
    if not rows:
        return "—"
    r = rows[0]
    ts = r["started_at"].strftime("%d.%m %H:%M")
    res = r.get("result") or {}
    anchor = res.get("anchor") if isinstance(res, dict) else None
    if r["status"] == "ok":
        upd = res.get("updated") if isinstance(res, dict) else None
        return f"{ts} • OK • anchor={anchor or '—'} • обновлено={upd if upd is not None else '—'}"
    if r["status"] == "dry_run":
        return f"{ts} • DRY • anchor={anchor or '—'}"
    if r["status"] == "error":
        err = (r.get("error_text") or "")[:80]
        return f"{ts} • ERROR • {err}"
    return f"{ts} • {r['status']}"


@router.callback_query(ParCb.filter(F.action == "view"))
async def cb_view(call: CallbackQuery, callback_data: ParCb) -> None:
    p = REGISTRY.get(callback_data.key)
    if not p:
        await call.answer("Не найдено", show_alert=True)
        return
    enabled = await is_enabled(p.key)
    cron = await get_cron(p.key)
    last = await _last_run_summary(p.key)
    is_super = await is_super_admin(call.from_user.id)
    text = (
        f"🚚 <b>{html.escape(p.label)}</b>\n"
        f"Сеть: <code>{html.escape(p.chain_name)}</code>\n"
        f"Включен: {'да' if enabled else 'нет'}\n"
        f"Cron: <code>{html.escape(cron)}</code>\n"
        f"Последний запуск: {html.escape(last)}"
    )
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="▶️ Запустить", callback_data=ParCb(action="run", key=p.key).pack())],
        [InlineKeyboardButton(text="🧪 Dry-run", callback_data=ParCb(action="dry", key=p.key).pack())],
        [InlineKeyboardButton(text="📜 Лог", callback_data=ParCb(action="logk", key=p.key).pack())],
    ]
    if is_super:
        rows.append([InlineKeyboardButton(
            text=("⛔️ Выключить" if enabled else "✅ Включить"),
            callback_data=ParCb(action="toggle", key=p.key).pack(),
        )])
        rows.append([InlineKeyboardButton(
            text="🕒 Изменить cron",
            callback_data=ParCb(action="cron", key=p.key).pack(),
        )])
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="adm:par:menu")])
    await safe_edit(call, text, InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


# ---------- run / dry ----------

async def _do_run(call: CallbackQuery, key: str, *, apply: bool, show_progress_text: bool = True) -> None:
    if show_progress_text:
        await call.message.answer("⏳ Запускаю…")
    await call.answer()
    is_super = await is_super_admin(call.from_user.id)
    scope = None if is_super else (await resolve_actor_scope(call.from_user.id) or [])
    try:
        result = await run_parser(
            key,
            triggered_by="manual",
            actor_tg_id=call.from_user.id,
            apply=apply,
            actor_scope=scope,
        )
    except Exception as e:
        log.exception("run_parser failed")
        await call.message.answer(f"❌ Ошибка: {html.escape(str(e))}")
        return
    if "error" in result:
        await call.message.answer(f"❌ {html.escape(result['error'])}")
        return
    # Import-mode parsers (e.g. sheets) carry richer stats.
    if "rows" in result:
        prefix = "🧪 Dry-run" if result.get("dry_run") else "✅ Импорт"
        msg = (
            f"{prefix}: строк {result.get('rows', 0)}\n"
            f"  Новых: {result.get('shops_new', 0)}\n"
            f"  Обновлено: {result.get('shops_updated', 0)}\n"
            f"  Завозов добавлено: {result.get('arrivals_added', 0)}\n"
            f"  Пропущено: {result.get('skipped', 0)}"
        )
        errs = result.get("errors") or []
        if errs:
            tail = "\n".join(html.escape(str(e)[:120]) for e in errs[:5])
            msg += f"\n\nОшибки (первые 5):\n<pre>{tail}</pre>"
        await call.message.answer(msg)
        return
    if result.get("dry_run"):
        await call.message.answer(
            f"🧪 Dry-run: anchor=<b>{result.get('anchor') or '—'}</b>"
        )
        return
    anchor = result.get("anchor") or "—"
    n = result.get("updated", 0)
    await call.message.answer(
        f"✅ anchor=<b>{html.escape(str(anchor))}</b>, обновлено магазинов: {n}"
    )


@router.callback_query(ParCb.filter(F.action == "run"))
async def cb_run(call: CallbackQuery, callback_data: ParCb) -> None:
    await _do_run(call, callback_data.key, apply=True)


@router.callback_query(ParCb.filter(F.action == "dry"))
async def cb_dry(call: CallbackQuery, callback_data: ParCb) -> None:
    await _do_run(call, callback_data.key, apply=False)


# ---------- toggle / cron ----------

@router.callback_query(ParCb.filter(F.action == "toggle"))
async def cb_toggle(call: CallbackQuery, callback_data: ParCb) -> None:
    if not await is_super_admin(call.from_user.id):
        await call.answer("Только супер-админ", show_alert=True)
        return
    enabled = await is_enabled(callback_data.key)
    new_val = "false" if enabled else "true"
    await cfg_set(
        f"parser.{callback_data.key}.enabled",
        new_val,
        type_="bool",
        description=f"Включён ли парсер {callback_data.key}",
        by=call.from_user.id,
    )
    await audit_write(
        call.from_user.id, "parser.toggle", "parser", callback_data.key,
        {"enabled": new_val},
    )
    await call.answer("Готово")
    callback_data_view = ParCb(action="view", key=callback_data.key)
    call.data = callback_data_view.pack()
    await cb_view(call, callback_data_view)


@router.callback_query(ParCb.filter(F.action == "cron"))
async def cb_cron_prompt(call: CallbackQuery, callback_data: ParCb, state: FSMContext) -> None:
    if not await is_super_admin(call.from_user.id):
        await call.answer("Только супер-админ", show_alert=True)
        return
    await state.set_state(ParserScheduleStates.cron)
    await state.update_data(parser_key=callback_data.key)
    cur = await get_cron(callback_data.key)
    await call.message.answer(
        f"Текущий cron: <code>{html.escape(cur)}</code>\n\n"
        "Введи новое выражение (формат как в Linux cron, 5 полей):\n"
        "<code>минута час день месяц день_недели</code>\n"
        "Например, <code>0 9 * * *</code> — каждый день в 9:00.\n"
        "Перезапуск шедулера произойдёт после рестарта бота.",
    )
    await call.answer()


@router.message(ParserScheduleStates.cron, F.text)
async def msg_cron(message: Message, state: FSMContext) -> None:
    raw = message.text.strip()
    if raw == "/cancel":
        await state.clear()
        await message.answer("Отменено.")
        return
    parts = raw.split()
    if len(parts) != 5:
        await message.answer("Нужно ровно 5 полей. Повтори.")
        return
    # quick sanity check via apscheduler
    try:
        from apscheduler.triggers.cron import CronTrigger
        CronTrigger.from_crontab(raw)
    except Exception as e:
        await message.answer(f"Не валидный cron: {html.escape(str(e))}")
        return
    data = await state.get_data()
    key = data.get("parser_key")
    if not key:
        await state.clear()
        await message.answer("Битый стейт.")
        return
    await cfg_set(
        f"parser.{key}.cron", raw, type_="str",
        description=f"Cron-расписание парсера {key}",
        by=message.from_user.id,
    )
    await audit_write(
        message.from_user.id, "parser.cron", "parser", key, {"cron": raw}
    )
    await state.clear()
    from services.scheduler import get_scheduler, reschedule_parser
    sched = get_scheduler()
    applied = False
    if sched is not None:
        applied = await reschedule_parser(sched, key)
    suffix = (
        "Шедулер подхватил новое расписание." if applied
        else "Применится после рестарта бота."
    )
    await message.answer(
        f"✅ Cron сохранён: <code>{html.escape(raw)}</code>\n{suffix}"
    )


# ---------- log views ----------

LOG_PAGE = 10


def _format_log_row(r: dict) -> str:
    ts = r["started_at"].strftime("%d.%m %H:%M:%S")
    by = r["triggered_by"]
    res = r.get("result")
    anchor = (res or {}).get("anchor") if isinstance(res, dict) else None
    if r["status"] == "ok":
        upd = (res or {}).get("updated")
        return f"<code>{ts}</code> #{r['id']} {r['parser_key']} OK by={by} anchor={anchor or '—'} upd={upd or 0}"
    if r["status"] == "dry_run":
        return f"<code>{ts}</code> #{r['id']} {r['parser_key']} DRY anchor={anchor or '—'}"
    if r["status"] == "error":
        err = (r.get("error_text") or "")[:60]
        return f"<code>{ts}</code> #{r['id']} {r['parser_key']} ERROR {html.escape(err)}"
    return f"<code>{ts}</code> #{r['id']} {r['parser_key']} {r['status']}"


@router.callback_query(ParCb.filter(F.action == "log"))
async def cb_log_all(call: CallbackQuery) -> None:
    rows = await list_runs(None, limit=LOG_PAGE)
    if not rows:
        await safe_edit(
            call, "Лог пуст.",
            InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="← Назад", callback_data="adm:par:menu")]]),
        )
        await call.answer()
        return
    text = "📜 <b>Последние запуски</b>\n\n" + "\n".join(_format_log_row(r) for r in rows)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="← Назад", callback_data="adm:par:menu")]])
    await safe_edit(call, text, kb)
    await call.answer()


@router.callback_query(ParCb.filter(F.action == "logk"))
async def cb_log_key(call: CallbackQuery, callback_data: ParCb) -> None:
    rows = await list_runs(callback_data.key, limit=LOG_PAGE)
    if not rows:
        await safe_edit(
            call, "Лог по этому парсеру пуст.",
            InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                text="← Назад", callback_data=ParCb(action="view", key=callback_data.key).pack(),
            )]]),
        )
        await call.answer()
        return
    text = (
        f"📜 <b>Последние запуски {callback_data.key}</b>\n\n"
        + "\n".join(_format_log_row(r) for r in rows)
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="← Назад", callback_data=ParCb(action="view", key=callback_data.key).pack(),
    )]])
    await safe_edit(call, text, kb)
    await call.answer()
