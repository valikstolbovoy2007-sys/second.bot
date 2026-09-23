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
_HEADER = "👥 <b>Все пользователи (старые сверху)</b>"


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
                   COALESCE(array_agg(s.name ORDER BY s.name), '{}') AS shops
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
    shops = u.get("shops") or []
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

    lines, shown, skipped = [_HEADER, ""], 0, 0
    for u in users:
        line = _user_line(u)
        if len("\n".join(lines)) + len(line) + 1 > _MAX_TEXT:
            skipped = len(users) - shown
            break
        lines.append(line)
        shown += 1
    if skipped > 0:
        lines.append(f"\n<i>… и ещё {skipped} не показаны (лимит сообщения)</i>")
    await safe_edit(call, "\n".join(lines), _back_kb())
    await call.answer()