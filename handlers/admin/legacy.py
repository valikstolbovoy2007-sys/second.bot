"""Legacy slash-commands for super-admins. New flows use the kbd-based panel."""
import html
import logging
from datetime import datetime

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from data.repos.admin_repo import stats
from data.repos.shops import (
    delete_shop,
    get_shop,
    list_all_shops,
    set_arrival,
    set_chain_arrival,
)
from handlers.admin.broadcasts import TEXT_PROMPT, _text_cancel_kb
from handlers.admin.filters import IsSuperAdmin
from services.megahand_parser import fetch_megahand_arrival
from states.admin_states import BroadcastStates

log = logging.getLogger(__name__)
router = Router(name="admin_legacy")

# All these commands are full-power: only super-admins.
router.message.filter(IsSuperAdmin())


def _chunks(s: str, size: int) -> list[str]:
    return [s[i:i + size] for i in range(0, len(s), size)]


async def _answer_in_place(message: Message, text: str) -> None:
    """Удаляет команду и отвечает единым сообщением."""
    try:
        await message.delete()
    except Exception:
        pass
    await message.answer(text)


@router.message(Command("list_shops"))
async def cmd_list_shops(message: Message) -> None:
    shops = await list_all_shops()
    if not shops:
        await _answer_in_place(message, "Магазинов пока нет.")
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
    try:
        await message.delete()
    except Exception:
        pass
    for chunk in _chunks("\n".join(lines), 3500):
        await message.answer(chunk)


@router.message(Command("set_arrival"))
async def cmd_set_arrival(message: Message, command: CommandObject) -> None:
    if not command.args:
        await _answer_in_place(message, "Использование: /set_arrival &lt;shop_id&gt; &lt;YYYY-MM-DD&gt;")
        return
    parts = command.args.split()
    if len(parts) != 2:
        await _answer_in_place(message, "Использование: /set_arrival &lt;shop_id&gt; &lt;YYYY-MM-DD&gt;")
        return
    try:
        shop_id = int(parts[0])
        anchor = datetime.strptime(parts[1], "%Y-%m-%d").date()
    except ValueError:
        await _answer_in_place(message, "Не смог распарсить. Дата в формате YYYY-MM-DD.")
        return
    ok = await set_arrival(shop_id, anchor)
    await _answer_in_place(message, "✅ Обновил" if ok else f"Магазин {shop_id} не найден.")


@router.message(Command("set_chain_arrival"))
async def cmd_set_chain(message: Message, command: CommandObject) -> None:
    if not command.args:
        await _answer_in_place(message, "Использование: /set_chain_arrival &lt;Chain&gt; &lt;YYYY-MM-DD&gt;")
        return
    parts = command.args.split()
    if len(parts) != 2:
        await _answer_in_place(message, "Использование: /set_chain_arrival &lt;Chain&gt; &lt;YYYY-MM-DD&gt;")
        return
    try:
        anchor = datetime.strptime(parts[1], "%Y-%m-%d").date()
    except ValueError:
        await _answer_in_place(message, "Дата в формате YYYY-MM-DD.")
        return
    n = await set_chain_arrival(parts[0], anchor)
    await _answer_in_place(message, f"✅ Обновил магазинов: {n}")


@router.message(Command("delete_shop"))
async def cmd_delete_shop(message: Message, command: CommandObject) -> None:
    if not command.args or not command.args.strip().isdigit():
        await _answer_in_place(message, "Использование: /delete_shop &lt;id&gt;")
        return
    shop_id = int(command.args.strip())
    shop = await get_shop(shop_id)
    if not shop:
        await _answer_in_place(message, f"Магазин {shop_id} не найден.")
        return
    ok = await delete_shop(shop_id)
    await _answer_in_place(message, "✅ Удалено" if ok else "Не нашёл")


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
    await _answer_in_place(message, "\n".join(lines))


@router.message(Command("import_megahand"))
async def cmd_import_megahand(message: Message) -> None:
    try:
        await message.delete()
    except Exception:
        pass
    status_msg = await message.answer("⏳ Парсю sevastopol.mhand.ru/promo/…")
    try:
        anchor = await fetch_megahand_arrival()
    except Exception as e:
        log.exception("megahand fetch failed")
        await status_msg.edit_text(f"❌ Ошибка: {html.escape(str(e))}")
        return
    if anchor is None:
        await status_msg.edit_text(
            "Не нашёл дат завоза на сайте.\n"
            "Используй: /set_chain_arrival Megahand &lt;YYYY-MM-DD&gt;"
        )
        return
    n = await set_chain_arrival("Megahand", anchor)
    await status_msg.edit_text(
        f"✅ Anchor для Megahand: <b>{anchor.strftime('%d.%m.%Y')}</b>\n"
        f"Обновил магазинов: {n}"
    )


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, state: FSMContext) -> None:
    """Переходит в новый визард рассылок: промпт — один экран, ввод удаляется."""
    await state.clear()
    await state.set_state(BroadcastStates.text)
    try:
        await message.delete()
    except Exception:
        pass
    msg = await message.answer(TEXT_PROMPT, reply_markup=_text_cancel_kb())
    await state.update_data(wizard_msg_id=msg.message_id)