from datetime import date

import pytest

from services.cycle import (
    CycleInfo,
    EventType,
    day_in_cycle,
    days_until,
    events_on,
    humanize_days,
    next_event_date,
)


ANCHOR = date(2026, 4, 1)
INFO_14 = CycleInfo(cycle_length=14, anchor_date=ANCHOR)
INFO_21 = CycleInfo(cycle_length=21, anchor_date=ANCHOR)


class TestDayInCycle:
    def test_anchor_is_day_zero(self) -> None:
        assert day_in_cycle(ANCHOR, INFO_14) == 0

    def test_day_after_anchor(self) -> None:
        assert day_in_cycle(date(2026, 4, 2), INFO_14) == 1

    def test_last_day_of_cycle(self) -> None:
        assert day_in_cycle(date(2026, 4, 14), INFO_14) == 13

    def test_wraps_to_next_cycle(self) -> None:
        assert day_in_cycle(date(2026, 4, 15), INFO_14) == 0

    def test_works_before_anchor(self) -> None:
        assert day_in_cycle(date(2026, 3, 31), INFO_14) == 13

    def test_far_future(self) -> None:
        assert day_in_cycle(date(2027, 4, 1), INFO_14) == (365 % 14)


class TestEventsOn:
    def test_arrival_on_anchor(self) -> None:
        assert events_on(ANCHOR, INFO_14) == {EventType.ARRIVAL}

    def test_max_discount_last_day(self) -> None:
        assert events_on(date(2026, 4, 14), INFO_14) == {EventType.MAX_DISCOUNT}

    def test_middle_for_14(self) -> None:
        # 14 // 2 = 7 → 2026-04-08
        assert events_on(date(2026, 4, 8), INFO_14) == {EventType.MIDDLE}

    def test_middle_for_21(self) -> None:
        # 21 // 2 = 10 → 2026-04-11
        assert events_on(date(2026, 4, 11), INFO_21) == {EventType.MIDDLE}

    def test_no_events_on_normal_day(self) -> None:
        assert events_on(date(2026, 4, 3), INFO_14) == set()

    def test_arrival_recurs_each_cycle(self) -> None:
        assert events_on(date(2026, 4, 15), INFO_14) == {EventType.ARRIVAL}
        assert events_on(date(2026, 4, 29), INFO_14) == {EventType.ARRIVAL}


class TestNextEventDate:
    def test_arrival_today_when_allowed(self) -> None:
        assert next_event_date(ANCHOR, INFO_14, EventType.ARRIVAL) == ANCHOR

    def test_arrival_today_skipped_when_not_allowed(self) -> None:
        result = next_event_date(ANCHOR, INFO_14, EventType.ARRIVAL, allow_today=False)
        assert result == date(2026, 4, 15)

    def test_arrival_from_mid_cycle(self) -> None:
        assert next_event_date(date(2026, 4, 5), INFO_14, EventType.ARRIVAL) == date(2026, 4, 15)

    def test_max_discount_from_anchor(self) -> None:
        assert next_event_date(ANCHOR, INFO_14, EventType.MAX_DISCOUNT) == date(2026, 4, 14)

    def test_middle_from_anchor(self) -> None:
        assert next_event_date(ANCHOR, INFO_14, EventType.MIDDLE) == date(2026, 4, 8)


class TestDaysUntil:
    def test_zero_on_event_day(self) -> None:
        assert days_until(ANCHOR, INFO_14, EventType.ARRIVAL) == 0

    def test_thirteen_to_max_discount_from_anchor(self) -> None:
        assert days_until(ANCHOR, INFO_14, EventType.MAX_DISCOUNT) == 13

    def test_seven_to_middle_from_anchor(self) -> None:
        assert days_until(ANCHOR, INFO_14, EventType.MIDDLE) == 7


class TestHumanizeDays:
    @pytest.mark.parametrize(
        ("n", "expected"),
        [
            (0, "сегодня"),
            (1, "завтра"),
            (2, "послезавтра"),
            (3, "через 3 дня"),
            (4, "через 4 дня"),
            (5, "через 5 дней"),
            (10, "через 10 дней"),
            (11, "через 11 дней"),
            (12, "через 12 дней"),
            (13, "через 13 дней"),
            (14, "через 14 дней"),
            (21, "через 21 день"),
            (22, "через 22 дня"),
            (25, "через 25 дней"),
            (101, "через 101 день"),
            (111, "через 111 дней"),
            (122, "через 122 дня"),
        ],
    )
    def test_humanize(self, n: int, expected: str) -> None:
        assert humanize_days(n) == expected


class TestCycleInfoValidation:
    def test_rejects_cycle_length_below_two(self) -> None:
        with pytest.raises(ValueError):
            CycleInfo(cycle_length=1, anchor_date=ANCHOR)
