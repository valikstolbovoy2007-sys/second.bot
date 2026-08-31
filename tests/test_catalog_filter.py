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
    SORT_PRICE,
    apply,
    compute_facts,
    matches,
    matches_search,
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
) -> Shop:
    return Shop(
        id=id, name=name, address=address, description=None,
        chain_name=chain_name,
        cycle_length=cycle_length, anchor_date=anchor_date,
        price_start=price_start, price_step=price_step,
        working_hours=None,
        is_active=True,
        maps_url=None,
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
