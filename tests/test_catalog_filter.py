"""Tests for services/catalog — filter and sort logic."""
from datetime import date, timedelta

from data.repos.shops import Shop
from services.catalog import (
    FLT_ALL,
    FLT_BY_PRICE,
    FLT_BY_WEIGHT,
    FLT_TRACKED,
    SORT_ARRIVAL,
    SORT_NAME,
    SORT_NEARBY,
    SORT_PRICE,
    apply,
    compute_facts,
    haversine_km,
    matches,
    matches_search,
    shop_distance_km,
)


def _shop(
    *,
    id: int = 1,
    name: str = "Магазин",
    address: str = "Тестовая, 1",
    chain_name: str | None = None,
    cycle_length: int | None = 14,
    anchor_date: date | None = None,
    price_start: int | None = 1200,
    price_step: int | None = 50,
    monthly_weekday: int | None = None,
    monthly_occurrence: int = 1,
    lat: float | None = None,
    lng: float | None = None,
) -> Shop:
    return Shop(
        id=id, name=name, address=address, description=None,
        chain_name=chain_name,
        cycle_length=cycle_length, anchor_date=anchor_date,
        price_start=price_start, price_step=price_step,
        working_hours=None,
        is_active=True,
        maps_url=None,
        monthly_weekday=monthly_weekday,
        monthly_occurrence=monthly_occurrence,
        lat=lat,
        lng=lng,
    )


# ---------- compute_facts ----------


def test_compute_facts_no_cycle():
    today = date(2026, 5, 11)
    f = compute_facts(_shop(cycle_length=None, anchor_date=None), today)
    assert f.day is None
    assert f.today_events == frozenset()
    assert f.price_today is None
    assert f.days_to_arrival is None


def test_compute_facts_arrival_today():
    today = date(2026, 5, 11)
    f = compute_facts(_shop(cycle_length=14, anchor_date=today), today)
    assert f.day == 0
    assert f.days_to_arrival == 0
    assert f.price_today == 1200  # day 0 → full price


def test_compute_facts_mid_cycle_price():
    today = date(2026, 5, 11)
    f = compute_facts(
        _shop(cycle_length=14, anchor_date=today - timedelta(days=5)),
        today,
    )
    assert f.day == 5
    assert f.price_today == 1200 - 5 * 50  # 950


# ---------- filter: tracked ----------


def test_filter_tracked_true():
    today = date(2026, 5, 11)
    f = compute_facts(_shop(), today)
    assert matches(f, FLT_TRACKED, is_tracked=True) is True
    assert matches(f, FLT_TRACKED, is_tracked=False) is False


# ---------- filter: by_weight / by_price ----------


def test_filter_by_weight_matches_shops_with_price_start():
    today = date(2026, 5, 11)
    priced = _shop(price_start=1200)
    unpriced = _shop(price_start=None)
    assert matches(compute_facts(priced, today), FLT_BY_WEIGHT, is_tracked=False) is True
    assert matches(compute_facts(unpriced, today), FLT_BY_WEIGHT, is_tracked=False) is False


def test_filter_by_price_matches_shops_without_price_start():
    today = date(2026, 5, 11)
    priced = _shop(price_start=1200)
    unpriced = _shop(price_start=None)
    assert matches(compute_facts(priced, today), FLT_BY_PRICE, is_tracked=False) is False
    assert matches(compute_facts(unpriced, today), FLT_BY_PRICE, is_tracked=False) is True


def test_filter_all_passes_everyone():
    today = date(2026, 5, 11)
    f = compute_facts(_shop(cycle_length=None, anchor_date=None), today)
    assert matches(f, FLT_ALL, is_tracked=False) is True


# ---------- search ----------


def test_search_matches_name():
    f = compute_facts(_shop(name="Megahand Pobedy"), date(2026, 5, 11))
    assert matches_search(f, "megahand") is True
    assert matches_search(f, "MEGA") is True
    assert matches_search(f, "smart") is False


def test_search_matches_address():
    f = compute_facts(_shop(address="ул. Победы, 12"), date(2026, 5, 11))
    assert matches_search(f, "победы") is True


def test_search_empty_passes_everyone():
    f = compute_facts(_shop(), date(2026, 5, 11))
    assert matches_search(f, "") is True
    assert matches_search(f, "   ") is True


def test_search_matches_chain():
    f = compute_facts(_shop(chain_name="Smart Second-Hand"), date(2026, 5, 11))
    assert matches_search(f, "smart") is True


# ---------- sort ----------


def test_sort_by_name_groups_chains():
    today = date(2026, 5, 11)
    a = _shop(id=1, name="Z-Shop", chain_name=None)
    b = _shop(id=2, name="A-Shop", chain_name="Megahand")
    c = _shop(id=3, name="A-Shop", chain_name=None)
    out = apply([a, b, c], today, flt=FLT_ALL, sort=SORT_NAME, search="", subscribed_ids=set())
    # Chain shops come first; independents last (NULLS LAST).
    assert [s.name for s in out] == ["A-Shop", "A-Shop", "Z-Shop"]
    assert [s.chain_name for s in out] == ["Megahand", None, None]


def test_sort_by_last_arrival_ascending():
    today = date(2026, 5, 11)
    # anchor_date = день завоза (day_in_cycle == 0 в этот день).
    a = _shop(id=1, name="A", cycle_length=14, anchor_date=today - timedelta(days=10))  # завоз 10 дн. назад
    b = _shop(id=2, name="B", cycle_length=14, anchor_date=today - timedelta(days=2))   # завоз 2 дн. назад
    c = _shop(id=3, name="C", cycle_length=14, anchor_date=today)                       # завоз сегодня
    out = apply([a, b, c], today, flt=FLT_ALL, sort=SORT_ARRIVAL, search="", subscribed_ids=set())
    # Недавние завозы сверху: сегодня → 2 дня назад → 10 дней назад.
    assert [s.name for s in out] == ["C", "B", "A"]


def test_sort_by_last_arrival_no_cycle_last():
    today = date(2026, 5, 11)
    a = _shop(id=1, name="A", cycle_length=14, anchor_date=today - timedelta(days=5))
    b = _shop(id=2, name="NoCycle", cycle_length=None, anchor_date=None)
    out = apply([a, b], today, flt=FLT_ALL, sort=SORT_ARRIVAL, search="", subscribed_ids=set())
    assert [s.name for s in out] == ["A", "NoCycle"]


def test_sort_by_price_cheapest_first():
    today = date(2026, 5, 11)
    # All same cycle, different anchor → different days_in_cycle → different prices.
    a = _shop(id=1, name="A", cycle_length=14, anchor_date=today - timedelta(days=10),
              price_start=1000, price_step=50)  # price = 1000-500 = 500
    b = _shop(id=2, name="B", cycle_length=14, anchor_date=today,
              price_start=1000, price_step=50)  # price = 1000 (day 0)
    c = _shop(id=3, name="C", cycle_length=14, anchor_date=today - timedelta(days=5),
              price_start=1000, price_step=50)  # price = 750
    out = apply([a, b, c], today, flt=FLT_ALL, sort=SORT_PRICE, search="", subscribed_ids=set())
    assert [s.name for s in out] == ["A", "C", "B"]


def test_sort_by_price_no_price_last():
    today = date(2026, 5, 11)
    a = _shop(id=1, name="A", cycle_length=14, anchor_date=today,
              price_start=500, price_step=10)
    b = _shop(id=2, name="NoPrice", cycle_length=None, anchor_date=None,
              price_start=None, price_step=None)
    out = apply([a, b], today, flt=FLT_ALL, sort=SORT_PRICE, search="", subscribed_ids=set())
    assert [s.name for s in out] == ["A", "NoPrice"]


# ---------- apply() integration ----------


def test_apply_combines_filter_and_search():
    today = date(2026, 5, 11)
    a = _shop(id=1, name="Megahand A", price_start=1200)
    b = _shop(id=2, name="Megahand B", price_start=None)
    c = _shop(id=3, name="Other", price_start=1200)
    out = apply(
        [a, b, c], today,
        flt=FLT_BY_WEIGHT, sort=SORT_NAME, search="megahand",
        subscribed_ids=set(),
    )
    # Has price_start ∧ name contains "megahand" → only `a`.
    assert [s.name for s in out] == ["Megahand A"]


def test_apply_tracked_filter():
    today = date(2026, 5, 11)
    a = _shop(id=1, name="A")
    b = _shop(id=2, name="B")
    c = _shop(id=3, name="C")
    out = apply(
        [a, b, c], today,
        flt=FLT_TRACKED, sort=SORT_NAME, search="",
        subscribed_ids={2, 3},
    )
    assert [s.name for s in out] == ["B", "C"]


# ---------- sort by distance (По расстоянию) ----------


def test_haversine_known_distance():
    # Москва → Санкт-Петербург ≈ 630-640 км.
    d = haversine_km(55.7558, 37.6173, 59.9343, 30.3351)
    assert 600 < d < 700


def test_haversine_same_point_is_zero():
    assert haversine_km(44.6, 33.5, 44.6, 33.5) < 1e-6


def test_shop_distance_km_none_without_coords():
    today = date(2026, 5, 11)
    f = compute_facts(_shop(id=1), today)
    assert shop_distance_km(f.shop, (44.6, 33.5)) is None
    assert shop_distance_km(f.shop, None) is None


def test_sort_nearby_nearest_first():
    today = date(2026, 5, 11)
    us = (44.6, 33.5)
    a = _shop(id=1, name="A", lat=44.6, lng=34.2)    # ~75 км
    b = _shop(id=2, name="B", lat=44.6, lng=33.52)   # ~2 км
    c = _shop(id=3, name="C", lat=44.61, lng=33.50)  # ~1.1 км
    out = apply(
        [a, b, c], today,
        flt=FLT_ALL, sort=SORT_NEARBY, search="", subscribed_ids=set(), point=us,
    )
    assert [s.name for s in out] == ["C", "B", "A"]


def test_sort_nearby_shops_without_coords_at_end():
    today = date(2026, 5, 11)
    us = (44.6, 33.5)
    far = _shop(id=1, name="Далеко", lat=44.6, lng=35.0)      # ~118 км
    near = _shop(id=2, name="Рядом", lat=44.6, lng=33.51)     # ~1 км
    nocoord_z = _shop(id=3, name="Без координат Z")
    nocoord_a = _shop(id=4, name="Без координат A")
    out = apply(
        [nocoord_z, far, nocoord_a, near], today,
        flt=FLT_ALL, sort=SORT_NEARBY, search="", subscribed_ids=set(), point=us,
    )
    # С координатами — по дистанции; без координат — в конец (по имени).
    assert [s.name for s in out] == ["Рядом", "Далеко", "Без координат A", "Без координат Z"]


def test_sort_nearby_combines_with_search():
    today = date(2026, 5, 11)
    us = (44.6, 33.5)
    keep = _shop(id=1, name="Megahand A", lat=44.6, lng=33.52)
    drop = _shop(id=2, name="Other", lat=44.6, lng=33.53)
    out = apply(
        [keep, drop], today,
        flt=FLT_ALL, sort=SORT_NEARBY, search="megahand", subscribed_ids=set(), point=us,
    )
    assert [s.name for s in out] == ["Megahand A"]


def test_catalog_kb_nearby_label_shows_distance():
    from keyboards.catalog_kb import catalog_kb

    s = _shop(id=1, name="Евро", lat=44.6, lng=33.5)
    kb = catalog_kb(
        [s], 0, FLT_ALL, SORT_NEARBY, 1, has_search=False,
        distances={1: 2.34},
    )
    texts = [b.text for row in kb.inline_keyboard for b in row]
    assert any("2,3 км ·" in t for t in texts)
    assert any("Евро" in t for t in texts)

    # Без distance-режима дистанция в подписи не появляется.
    kb_plain = catalog_kb([s], 0, FLT_ALL, SORT_NAME, 1, has_search=False)
    texts_plain = [b.text for row in kb_plain.inline_keyboard for b in row]
    assert not any("км" in t for t in texts_plain)


# ---------- payload wiring: tg-id vs db-id ----------


class _FakeTexts:
    """Minimal stub for texts.t: returns defaults for keys used by the header."""

    def __init__(self) -> None:
        self._tpl = {
            "catalog.title": "🛍 Каталог",
            "catalog.found": "Найдено: {count}",
            "catalog.legend": "📌 легенда",
            "catalog.hint": "<i>подсказка</i>",
            "catalog.empty": "пусто",
            "catalog.empty_filter": "ничего не найдено",
        }

    async def t(self, key: str, **kwargs):
        tpl = self._tpl.get(key, key)
        return tpl.format(**kwargs)


def test_catalog_payload_search_keyed_by_tg_id(monkeypatch):
    """Поиск хранится под tg-id юзера и применяется в payload даже если
    db-id (=users.id из upsert_user) не совпадает с tg-id (регресс: баг,
    когда keep-alive поиска искался по db-id и всегда возвращал '')."""
    import asyncio

    import handlers.catalog as cat_mod

    a = _shop(id=1, name="Полтавская", address="Полтавская, 7а")
    b = _shop(id=2, name="Невский проспект", address="Невский, 100")

    async def fake_list_active_shops():
        return [a, b]

    async def fake_subscribed_shop_ids(db_user_id):
        return set()

    fake = _FakeTexts()
    monkeypatch.setattr(cat_mod, "list_active_shops", fake_list_active_shops)
    monkeypatch.setattr(cat_mod, "subscribed_shop_ids", fake_subscribed_shop_ids)
    monkeypatch.setattr(cat_mod, "t", fake.t)

    tg_id, db_id = 777_000_123, 42  # tg-id и db-id гарантированно разные
    cat_mod._user_search[tg_id] = "полтавская"

    async def run():
        body, kb = await cat_mod._catalog_payload(
            db_id, tg_id=tg_id, page=0, flt=FLT_ALL, sort=SORT_NAME,
        )
        return body, kb

    body, kb = asyncio.run(run())
    # Применён поиск: в шапке запрос, из двух магазинов остался один.
    assert "Результаты по поиску" in body
    assert "не найдено" not in body
    assert "Найдено: 1" in body
    button_texts = [b.text for row in kb.inline_keyboard for b in row]
    assert any("Полтавская" in t for t in button_texts)
    assert not any("Невский" in t for t in button_texts)

    # Без активного поиска (другой tg-id) — полный список.
    async def run_all():
        return await cat_mod._catalog_payload(
            db_id, tg_id=tg_id + 1, page=0, flt=FLT_ALL, sort=SORT_NAME,
        )

    body2, kb2 = asyncio.run(run_all())
    assert "Найдено: 2" in body2
    button2 = [b.text for row in kb2.inline_keyboard for b in row]
    assert any("Невский" in t for t in button2)
