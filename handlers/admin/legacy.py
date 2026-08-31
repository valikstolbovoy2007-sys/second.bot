"""Legacy slash-commands for super-admins. New flows use the kbd-based panel."""
import asyncio
import html
import logging
from datetime import datetime

from aiogram import Bot, Router
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from data.repos.admin_repo import all_user_tg_ids, stats
from data.repos.shops import (
    delete_shop,
    get_shop,
    list_all_shops,
    set_arrival,
    set_chain_arrival,
)
from handlers.admin.filters import IsSuperAdmin
from services.megahand_parser import fetch_megahand_arrival
from states.admin_states import BroadcastStates

log = logging.getLogger(__name__)
router = Router(name="admin_legacy")

# All these commands are full-power: only super-admins.
router.message.filter(IsSuperAdmin())


def _chunks(s: str, size: int) -> list[str]:
    return [s[i:i + size] for i in range(0, len(s), size)]


@router.message(Command("list_shops"))
async def cmd_list_shops(message: Message) -> None:
    shops = await list_all_shops()
    if not shops:
        await message.answer("Магазинов пока нет.")
        return
    lines = ["📋 <b>Все магазины:</b>", ""]
    for s in shops:
        chain = f"[{s.chain_name}] " if s.chain_name else ""
        cycle = f"{s.cycle_length}д" if s.cycle_length else "—"
        anchor = s.anchor_date.strftime("%d.%m") if s.anchor_date else "—"
        active = "" if s.is_active else " 🚫"
        lines.append(
            f"<code>{s.id}</code>: {chain}{html.escape(s.name)}{active}\n"
            f"   📍 {html.escape(s.address)} | цикл {cycle} | anchor {anchor}"
        )
    text = "\n".join(lines)
    for chunk in _chunks(text, 3500):
        await message.answer(chunk)


@router.message(Command("set_arrival"))
async def cmd_set_arrival(message: Message, command: CommandObject) -> None:
    if not command.args:
        await message.answer("Использование: /set_arrival &lt;shop_id&gt; &lt;YYYY-MM-DD&gt;")
        return
    parts = command.args.split()
    if len(parts) != 2:
        await message.answer("Использование: /set_arrival &lt;shop_id&gt; &lt;YYYY-MM-DD&gt;")
        return
    try:
        shop_id = int(parts[0])
        anchor = datetime.strptime(parts[1], "%Y-%m-%d").date()
    except ValueError:
        await message.answer("Не смог распарсить. Дата в формате YYYY-MM-DD.")
        return
    ok = await set_arrival(shop_id, anchor)
    await message.answer("✅ Обновил" if ok else f"Магазин {shop_id} не найден.")


@router.message(Command("set_chain_arrival"))
async def cmd_set_chain(message: Message, command: CommandObject) -> None:
    if not command.args:
        await message.answer("Использование: /set_chain_arrival &lt;Chain&gt; &lt;YYYY-MM-DD&gt;")
        return
    parts = command.args.split()
    if len(parts) != 2:
        await message.answer("Использование: /set_chain_arrival &lt;Chain&gt; &lt;YYYY-MM-DD&gt;")
        return
    try:
        anchor = datetime.strptime(parts[1], "%Y-%m-%d").date()
    except ValueError:
        await message.answer("Дата в формате YYYY-MM-DD.")
        return
    n = await set_chain_arrival(parts[0], anchor)
    await message.answer(f"✅ Обновил магазинов: {n}")


@router.message(Command("delete_shop"))
async def cmd_delete_shop(message: Message, command: CommandObject) -> None:
    if not command.args or not command.args.strip().isdigit():
        await message.answer("Использование: /delete_shop &lt;id&gt;")
        return
    shop_id = int(command.args.strip())
    shop = await get_shop(shop_id)
    if not shop:
        await message.answer(f"Магазин {shop_id} не найден.")
        return
    ok = await delete_shop(shop_id)
    await message.answer("✅ Удалено" if ok else "Не нашёл")


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    s = await stats()
    lines = [
        "📊 <b>Статистика</b>",
        "",
        f"👥 Пользователей: {s['users_total']} (активных: {s['users_active']})",
        f"⭐ Подписок: {s['subs_total']}",
        f"🛍 Магазинов: {s['shops_total']}",
        f"📤 Отправлено сегодня: {s['sent_today']}",
    ]
    if s["top_shops"]:
        lines.append("")
        lines.append("<b>Топ-10 магазинов по подпискам:</b>")
        for chain, name, n in s["top_shops"]:
            chain_p = f"[{chain}] " if chain else ""
            lines.append(f"   {chain_p}{html.escape(name)} — {n}")
    await message.answer("\n".join(lines))


@router.message(Command("import_megahand"))
async def cmd_import_megahand(message: Message) -> None:
    await message.answer("⏳ Парсю sevastopol.mhand.ru/promo/…")
    try:
        anchor = await fetch_megahand_arrival()
    except Exception as e:
        log.exception("megahand fetch failed")
        await message.answer(f"❌ Ошибка: {html.escape(str(e))}")
        return
    if anchor is None:
        await message.answer(
            "Не нашёл дат завоза на сайте.\n"
            "Используй: /set_chain_arrival Megahand &lt;YYYY-MM-DD&gt;"
        )
        return
    n = await set_chain_arrival("Megahand", anchor)
    await message.answer(
        f"✅ Anchor для Megahand: <b>{anchor.strftime('%d.%m.%Y')}</b>\n"
        f"Обновил магазинов: {n}"
    )


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(BroadcastStates.text)
    await message.answer(
        "📣 Введи текст рассылки (HTML поддерживается). /cancel для отмены.",
    )


@router.message(BroadcastStates.text, Command("cancel"))
async def broadcast_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено.")


@router.message(BroadcastStates.text)
async def broadcast_capture(message: Message, state: FSMContext, bot: Bot) -> None:
    text = message.html_text
    await state.clear()
    await message.answer("📤 Отправляю…")
    tg_ids = await all_user_tg_ids()
    sent = failed = 0
    for tg_id in tg_ids:
        try:
            await bot.send_message(tg_id, text)
            sent += 1
        except TelegramForbiddenError:
            failed += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                await bot.send_message(tg_id, text)
                sent += 1
            except Exception:
                failed += 1
        except Exception:
            log.exception("broadcast send failed for %s", tg_id)
            failed += 1
        await asyncio.sleep(0.05)
    await message.answer(f"📊 Отправлено: {sent}, ошибок: {failed}")
