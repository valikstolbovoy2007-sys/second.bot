"""Catalog filtering and sorting.

Loads today's "facts" per shop (cycle phase, today's events, today's price,
days until next arrival) and applies the user's chosen filter and sort.

The catalog has ~tens of shops, so we filter in Python — the cycle math is
not worth duplicating in SQL.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from data.repos.shops import Shop
from services.cycle import (
    CycleInfo,
    EventType,
    day_in_cycle,
    days_until,
    events_on,
)

# ---------- filter / sort codes ----------

FLT_ALL = "all"
FLT_TRACKED = "tracked"
FLT_BY_WEIGHT = "by_weight"
FLT_BY_PRICE = "by_price"

VALID_FILTERS = {
    FLT_ALL,
    FLT_TRACKED,
    FLT_BY_WEIGHT,
    FLT_BY_PRICE,
}

# Filters surfaced behind the "⋯ Ещё" sub-screen.
MORE_FILTERS = (
    FLT_BY_WEIGHT,
    FLT_BY_PRICE,
)

SORT_NAME = "name"
SORT_ARRIVAL = "next_arrival"
SORT_PRICE = "price"

VALID_SORTS = {SORT_NAME, SORT_ARRIVAL, SORT_PRICE}


# ---------- facts ----------


@dataclass(frozen=True)
class ShopFacts:
    shop: Shop
    day: int | None              # day_in_cycle (0..cycle-1) or None
    today_events: frozenset[EventType]
    price_today: int | None
    days_to_arrival: int | None  # None if no cycle


def compute_facts(shop: Shop, today: date) -> ShopFacts:
    if not (shop.cycle_length and shop.anchor_date):
        return ShopFacts(shop, None, frozenset(), None, None)
    info = CycleInfo(shop.cycle_length, shop.anchor_date)
    d = day_in_cycle(today, info)
    events = frozenset(events_on(today, info))
    price: int | None = None
    if shop.price_start and shop.price_step is not None:
        price = max(0, shop.price_start - max(0, d) * shop.price_step)
    return ShopFacts(
        shop=shop,
        day=d,
        today_events=events,
        price_today=price,
        days_to_arrival=days_until(today, info, EventType.ARRIVAL),
    )


# ---------- filter / sort ----------


def matches(facts: ShopFacts, flt: str, *, is_tracked: bool) -> bool:
    if flt == FLT_ALL:
        return True
    if flt == FLT_TRACKED:
        return is_tracked
    if flt == FLT_BY_WEIGHT:
        return bool(facts.shop.price_start)
    if flt == FLT_BY_PRICE:
        return not facts.shop.price_start
    return True


def matches_search(facts: ShopFacts, query: str) -> bool:
    q = query.strip().lower()
    if not q:
        return True
    shop = facts.shop
    if q in shop.name.lower():
        return True
    if q in (shop.address or "").lower():
        return True
    if shop.chain_name and q in shop.chain_name.lower():
        return True
    return False


_BIG = 10**9


def sort_key(facts: ShopFacts, sort: str):
    if sort == SORT_ARRIVAL:
        # facts.day == дней с прошлого завоза (0 — завоз сегодня, 1 — был
        # вчера, и т.д.). Сортируем по возрастанию: недавние завозы сверху.
        da = facts.day if facts.day is not None else _BIG
        return (da, facts.shop.name.lower())
    if sort == SORT_PRICE:
        # Магазины без цены — в конец.
        p = facts.price_today if facts.price_today is not None else _BIG
        return (p, facts.shop.name.lower())
    # SORT_NAME (default): сеть → имя
    chain = (facts.shop.chain_name or "\uffff").lower()
    return (chain, facts.shop.name.lower())


def apply(
    shops: list[Shop],
    today: date,
    *,
    flt: str,
    sort: str,
    search: str,
    subscribed_ids: set[int],
) -> list[Shop]:
    """Returns a filtered + sorted list of shops."""
    facts = [compute_facts(s, today) for s in shops]
    filtered = [
        f for f in facts
        if matches(f, flt, is_tracked=f.shop.id in subscribed_ids)
        and matches_search(f, search)
    ]
    filtered.sort(key=lambda f: sort_key(f, sort))
    return [f.shop for f in filtered]
