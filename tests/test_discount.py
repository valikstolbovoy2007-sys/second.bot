"""Tests for the custom per-day discount schedule (services/discount)."""
from datetime import date

from data.repos.shops import Shop
from services.card_render import format_price_schedule
from services.card_template import build_context
from services.discount import label_for_day, labels, parse_schedule


def _shop(discount_schedule: str | None, *, price_start: int | None = None) -> Shop:
    return Shop(
        id=1, name="Megahend", address="Тестовая, 1", description=None,
        chain_name=None, cycle_length=21, anchor_date=date(2026, 9, 5),
        price_start=price_start, price_step=50,
        working_hours=None, is_active=True, maps_url=None,
        monthly_weekday=None, monthly_occurrence=1,
        discount_schedule=discount_schedule,
    )


# ---------- parse ----------


class TestParseSchedule:
    def test_empty_returns_empty_list(self) -> None:
        assert parse_schedule(None) == []
        assert parse_schedule("") == []

    def test_splits_lines(self) -> None:
        assert parse_schedule("Новый завоз\n70% скидка\n") == ["Новый завоз", "70% скидка"]

    def test_strips_whitespace(self) -> None:
        assert parse_schedule("  Новый завоз  \n\t70% скидка\n") == ["Новый завоз", "70% скидка"]

    def test_blank_lines_dropped(self) -> None:
        assert parse_schedule("\nНовый завоз\n\n70% скидка\n") == ["Новый завоз", "70% скидка"]


# ---------- label_for_day ----------


SCHEDULE = "\n".join([
    "Новый завоз",       # day 0
    "70% скидка",        # day 1
    "70% скидка",        # day 2
    "80% скидка",        # day 3
])


class TestLabelForDay:
    def test_returns_label_for_day(self) -> None:
        shop = _shop(SCHEDULE)
        assert label_for_day(shop, 0) == "Новый завоз"
        assert label_for_day(shop, 2) == "70% скидка"

    def test_empty_schedule(self) -> None:
        assert label_for_day(_shop(None), 0) == ""

    def test_beyond_schedule_returns_empty(self) -> None:
        assert label_for_day(_shop(SCHEDULE), 4) == ""

    def test_labels_helper(self) -> None:
        assert labels(_shop(SCHEDULE)) == ["Новый завоз", "70% скидка", "70% скидка", "80% скидка"]


# ---------- build_context ----------


class TestBuildContext:
    def test_card_uses_schedule_label_today(self) -> None:
        shop = _shop(SCHEDULE)
        ctx = build_context(shop, date(2026, 9, 6))  # день после завоза (day 1)
        assert "70% скидка" in ctx["price_today_line"]
        assert "Сегодня" in ctx["price_today_line"]
        assert "2-й день" in ctx["day_label"]

    def test_card_arrival_day_label(self) -> None:
        shop = _shop(SCHEDULE)
        ctx = build_context(shop, date(2026, 9, 5))  # завоз (day 0)
        assert "Новый завоз" in ctx["price_today_line"]

    def test_card_ignores_price_formula_when_schedule(self) -> None:
        shop = _shop(SCHEDULE, price_start=1000)
        ctx = build_context(shop, date(2026, 9, 6))
        assert "₽/кг" not in ctx["price_today_line"]
        assert "1000" not in ctx["price_today_line"]


# ---------- format_price_schedule ----------


class TestFormatSchedule:
    def test_schedule_mode_header(self) -> None:
        shop = _shop(SCHEDULE)
        text = format_price_schedule(shop, date(2026, 9, 6))
        assert "Расписание скидок" in text
        assert "Цикл: 21 дн." in text

    def test_schedule_mode_shows_labels(self) -> None:
        shop = _shop(SCHEDULE)
        text = format_price_schedule(shop, date(2026, 9, 6))
        assert "70% скидка" in text
        assert "80% скидка" in text
        assert "Новый завоз" in text

    def test_no_schedule_and_no_price_falls_back(self) -> None:
        shop = _shop(None)
        text = format_price_schedule(shop, date(2026, 9, 6))
        assert "Цены пока не уточнили" in text

    def test_no_schedule_with_price_uses_price_mode(self) -> None:
        shop = _shop(None, price_start=1000)
        text = format_price_schedule(shop, date(2026, 9, 6))
        assert "Расписание цен" in text

    def test_schedule_without_cycle_falls_back(self) -> None:
        shop = Shop(
            id=1, name="Megahend", address="Тестовая, 1", description=None,
            chain_name=None, cycle_length=None, anchor_date=None,
            price_start=None, price_step=None,
            working_hours=None, is_active=True, maps_url=None,
            monthly_weekday=None, monthly_occurrence=1,
            discount_schedule="Новый завоз",
        )
        text = format_price_schedule(shop, date(2026, 9, 6))
        assert "Расписание завозов уточняется" in text


# ---------- shop_has_price_schedule ----------


class TestHasPriceSchedule:
    def test_true_with_schedule_only(self) -> None:
        assert _shop(SCHEDULE).discount_schedule or True
        from data.repos.shops import shop_has_price_schedule
        assert shop_has_price_schedule(_shop(SCHEDULE))

    def test_true_with_price_formula(self) -> None:
        from data.repos.shops import shop_has_price_schedule
        assert shop_has_price_schedule(_shop(None, price_start=1000))

    def test_false_with_nothing(self) -> None:
        from data.repos.shops import shop_has_price_schedule
        assert not shop_has_price_schedule(_shop(None))