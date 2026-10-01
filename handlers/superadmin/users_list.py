import html
import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from data.db import pool
from handlers.admin.filters import IsSuperAdmin
from handlers.admin.ui import safe_edit

log = logging.getLogger(__name__)
router = Router(name="sa_users_list")
router.callback_query.filter(IsSuperAdmin())

# Лимит Telegram на длину текстового сообщения.
_MAX_TEXT = 3900
# Заголовок + строка ост-флага.
_HEADER = "👥 <b>Все пользователи (новые снизу)</b>"


def _back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← В суперадминку", callback_data="sa:menu")],
    ])


async def _all_users_with_shops() -> list[dict]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT u.username,
                   u.created_at,
                   COALESCE(array_agg(s.name ORDER BY s.name) FILTER (WHERE s.name IS NOT NULL), '{}') AS shops
            FROM users u
            LEFT JOIN subscriptions sub ON sub.user_id = u.id
            LEFT JOIN shops s ON s.id = sub.shop_id
            GROUP BY u.id, u.username, u.created_at
            ORDER BY u.created_at ASC, u.id ASC
            """
        )
    return [dict(r) for r in rows]


def _user_line(u: dict) -> str:
    name = u["username"]
    label = f"@{html.escape(name)}" if name else "<i>(без username)</i>"
    created = u["created_at"].strftime("%d.%m.%Y") if u["created_at"] else "?"
    shops = [s for s in (u.get("shops") or []) if s]
    if shops:
        shops_txt = ", ".join(html.escape(s) for s in shops)
        return f"• {label} <i>({created})</i> — {shops_txt}"
    return f"• {label} <i>({created})</i> — без подписок"


@router.callback_query(F.data == "sa:users:list")
async def cb_users_list(call: CallbackQuery) -> None:
    users = await _all_users_with_shops()
    if not users:
        await safe_edit(call, "Пользователей пока нет.", _back_kb())
        await call.answer()
        return

    lines_all = [_user_line(u) for u in users]
    # Список хронологический (новые снизу). Если всё не влезает —
    # отбрасываем старых сверху и показываем хвост самых новых, что влезает.
    lines_chosen: list[str] = []
    start = len(lines_all)
    for i in range(len(lines_all) - 1, -1, -1):
        ln = lines_all[i]
        if len("\n".join(lines_chosen)) + len(ln) + 1 > _MAX_TEXT:
            break
        lines_chosen.insert(0, ln)
        start = i
    skipped = start
    if skipped > 0:
        lines_chosen[-1] = f"{lines_chosen[-1]}\n<i>… и ещё {skipped} старых не показаны (лимит сообщения)</i>"
    await safe_edit(call, "\n".join([_HEADER, ""] + lines_chosen), _back_kb())
    await call.answer()