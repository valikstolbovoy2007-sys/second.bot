"""Catalog filtering and sorting.

Loads today's "facts" per shop (cycle phase, today's events, today's price,
days until next arrival) and applies the user's chosen filter and sort.

The catalog has ~tens of shops, so we filter in Python — the cycle math is
not worth duplicating in SQL.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from data.repos.shops import Shop
from services.cycle import (
    EventType,
    day_in_cycle,
    days_until,
    events_on,
    resolve_cycle_info,
)

# ---------- filter / sort codes ----------

FLT_ALL = "all"
FLT_TRACKED = "tracked"
FLT_BY_WEIGHT = "by_weight"
FLT_BY_PRICE = "by_price"
FLT_NEARBY = "nearby"

VALID_FILTERS = {
    FLT_ALL,
    FLT_TRACKED,
    FLT_BY_WEIGHT,
    FLT_BY_PRICE,
    FLT_NEARBY,
}

# Filters surfaced behind the "⋯ Ещё" sub-screen.
MORE_FILTERS = (
    FLT_BY_WEIGHT,
    FLT_BY_PRICE,
    FLT_NEARBY,
)

SORT_NAME = "name"
SORT_ARRIVAL = "next_arrival"
SORT_PRICE = "price"

VALID_SORTS = {SORT_NAME, SORT_ARRIVAL, SORT_PRICE}

# Максимальная дистанция для фильтра «По расстоянию» (км). Дальше города
# секондов нет смысла показывать.
NEARBY_MAX_KM = 100.0

_EARTH_RADIUS_KM = 6371.0


# ---------- facts ----------


@dataclass(frozen=True)
class ShopFacts:
    shop: Shop
    day: int | None              # day_in_cycle (0..cycle-1) or None
    today_events: frozenset[EventType]
    price_today: int | None
    days_to_arrival: int | None  # None if no cycle


def compute_facts(shop: Shop, today: date) -> ShopFacts:
    info = resolve_cycle_info(shop.cycle_length, shop.anchor_date, shop.monthly_weekday, today, monthly_occurrence=shop.monthly_occurrence)
    if info is None:
        return ShopFacts(shop, None, frozenset(), None, None)
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


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Расстояние между двумя точками по формуле гаверсинуса, в километрах."""
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    d_lat = lat2 - lat1
    d_lon = lon2 - lon1
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(d_lon / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def shop_distance_km(shop: Shop, point: tuple[float, float] | None) -> float | None:
    """Км от точки пользователя до магазина; None если неизвестно."""
    if point is None or shop.lat is None or shop.lng is None:
        return None
    return haversine_km(point[0], point[1], shop.lat, shop.lng)


# ---------- filter / sort ----------


def matches(
    facts: ShopFacts, flt: str, *, is_tracked: bool,
    point: tuple[float, float] | None = None,
) -> bool:
    if flt == FLT_ALL:
        return True
    if flt == FLT_TRACKED:
        return is_tracked
    if flt == FLT_BY_WEIGHT:
        return bool(facts.shop.price_start)
    if flt == FLT_BY_PRICE:
        return not facts.shop.price_start
    if flt == FLT_NEARBY:
        if point is None:
            return False
        distance = shop_distance_km(facts.shop, point)
        return distance is not None and distance <= NEARBY_MAX_KM
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
    point: tuple[float, float] | None = None,
) -> list[Shop]:
    """Returns a filtered + sorted list of shops.

    `point` — (lat, lon) координаты пользователя. Обязателен для FLT_NEARBY:
    без него магазины не проходят фильтр. Переданный `sort` игнорируется —
    «По расстоянию» всегда сортирует по дистанции (ближайшие сверху).
    """
    facts = [compute_facts(s, today) for s in shops]
    filtered = [
        f for f in facts
        if matches(f, flt, is_tracked=f.shop.id in subscribed_ids, point=point)
        and matches_search(f, search)
    ]
    if flt == FLT_NEARBY:
        filtered.sort(key=lambda f: (shop_distance_km(f.shop, point) or _BIG, f.shop.name.lower()))
    else:
        filtered.sort(key=lambda f: sort_key(f, sort))
    return [f.shop for f in filtered]
