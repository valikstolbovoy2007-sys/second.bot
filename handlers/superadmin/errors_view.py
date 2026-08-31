"""Superadmin errors browser.

Groups error_log rows by signature, supports paginating, viewing the latest
record per signature, and bulk-resolving a signature.
"""
import html
import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from handlers.admin.filters import IsSuperAdmin
from handlers.admin.ui import safe_edit
from services.audit import write as audit_write
from services.error_log import (
    get_error,
    list_by_signature,
    list_groups,
    resolve_signature,
)

log = logging.getLogger(__name__)
router = Router(name="sa_errors")
router.message.filter(IsSuperAdmin())
router.callback_query.filter(IsSuperAdmin())

PAGE_SIZE = 10


def _back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="← Назад", callback_data="sa:menu")]]
    )


@router.callback_query(F.data.startswith("sa:err:list:"))
async def cb_list(call: CallbackQuery) -> None:
    parts = call.data.split(":")
    # sa:err:list:<page>[:all|open]
    try:
        page = int(parts[3])
    except (ValueError, IndexError):
        page = 0
    only_unresolved = not (len(parts) > 4 and parts[4] == "all")

    groups = await list_groups(PAGE_SIZE, page * PAGE_SIZE, only_unresolved=only_unresolved)
    title = "🐞 <b>Ошибки</b> (открытые)" if only_unresolved else "🐞 <b>Ошибки</b> (все)"

    if not groups:
        await safe_edit(call, f"{title}\n\n— пусто —", _back_kb())
        await call.answer()
        return

    lines = [title, ""]
    rows: list[list[InlineKeyboardButton]] = []
    for g in groups:
        last_ts = g["last_ts"].strftime("%d.%m %H:%M")
        msg = (g["last_message"] or "—").splitlines()[0][:60]
        lines.append(f"<code>{g['signature']}</code> • ×{g['count']} • {last_ts}\n  {html.escape(msg)}")
        rows.append([
            InlineKeyboardButton(
                text=f"🔍 {g['signature']} (×{g['count']})",
                callback_data=f"sa:err:view:{g['signature']}",
            ),
        ])

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        suffix = ":all" if not only_unresolved else ""
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"sa:err:list:{page - 1}{suffix}"))
    nav.append(InlineKeyboardButton(text=f"стр {page + 1}", callback_data="noop"))
    if len(groups) == PAGE_SIZE:
        suffix = ":all" if not only_unresolved else ""
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"sa:err:list:{page + 1}{suffix}"))
    rows.append(nav)

    toggle = InlineKeyboardButton(
        text="📂 Все" if only_unresolved else "🟢 Только открытые",
        callback_data="sa:err:list:0:all" if only_unresolved else "sa:err:list:0",
    )
    rows.append([toggle])
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="sa:menu")])

    await safe_edit(call, "\n\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


@router.callback_query(F.data.startswith("sa:err:view:"))
async def cb_view(call: CallbackQuery) -> None:
    signature = call.data.split(":", 3)[3]
    items = await list_by_signature(signature, limit=10)
    if not items:
        await safe_edit(call, "Запись не найдена.", _back_kb())
        await call.answer()
        return
    last = items[0]
    err = await get_error(int(last["id"]))
    if not err:
        await safe_edit(call, "Запись не найдена.", _back_kb())
        await call.answer()
        return
    tb = (err["traceback"] or "")[-1500:]
    text = (
        f"🐞 <b>{html.escape(signature)}</b>\n"
        f"Источник: {html.escape(str(err['source'] or '—'))}\n"
        f"Уровень: {html.escape(err['level'] or 'ERROR')}\n"
        f"Последний раз: {err['ts'].strftime('%d.%m.%Y %H:%M:%S')}\n"
        f"Всего записей: {len(items)}\n"
        f"Resolved: {'да' if err['resolved'] else 'нет'}\n\n"
        f"<b>Сообщение:</b>\n<code>{html.escape((err['message'] or '')[:400])}</code>\n\n"
        f"<b>Traceback (последние 1500 символов):</b>\n<pre>{html.escape(tb)}</pre>"
    )
    rows = [
        [InlineKeyboardButton(text="✅ Resolved", callback_data=f"sa:err:resolve:{signature}")],
        [InlineKeyboardButton(text="← К списку", callback_data="sa:err:list:0")],
    ]
    await safe_edit(call, text, InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


@router.callback_query(F.data.startswith("sa:err:resolve:"))
async def cb_resolve(call: CallbackQuery) -> None:
    signature = call.data.split(":", 3)[3]
    n = await resolve_signature(signature)
    await audit_write(
        call.from_user.id,
        "error.resolve",
        "error_signature",
        signature,
        {"count": n},
    )
    await call.answer(f"Закрыто {n} записей", show_alert=False)
    # rerender list
    call.data = "sa:err:list:0"
    await cb_list(call)
