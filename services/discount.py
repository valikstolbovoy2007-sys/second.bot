"""Кастомный график скидок магазина.

Хранится в `shops.discount_schedule` как текст: ОДНА СТРОКА = метка дня
цикла N (N=0 — день завоза). Длина не обязана совпадать с cycle_length:
если график короче, после последней метки просто нет данных — `label_for_day`
возвращает пустую строку. Если длиннее — лишние дни игнорируются.

График полностью заменяет ₽-формулу: в карточке и в «Расписании цен»
показываем метку дня, а не цену.
"""
from __future__ import annotations

from data.repos.shops import Shop


def parse_schedule(raw: str | None) -> list[str]:
    """Сырой текст графика → список меток дня (0 = день завоза).

    Пустые/пробельные строки отбрасываются: редактор в админке сам требует
    точное число строк (по одной на день цикла), поэтому оставлять пустые
    «дырки» не нужно — лишняя пустая строка сдвинула бы дни.
    """
    if not raw:
        return []
    return [line.strip() for line in raw.splitlines() if line.strip()]


def labels(shop: Shop) -> list[str]:
    return parse_schedule(shop.discount_schedule)


def label_for_day(shop: Shop, day_in_cycle: int) -> str:
    """Метка для дня цикла; пустая строка, если не задана."""
    schedule = labels(shop)
    if not schedule:
        return ""
    if day_in_cycle >= len(schedule):
        return ""
    return schedule[day_in_cycle]