from datetime import date

from services.notifier import (
    Trigger,
    format_message,
    pick_events_for_subscription,
)

ANCHOR = date(2026, 4, 1)


def _sub(**overrides) -> dict:
    base = {
        "shop_id": 1,
        "name": "Megahand",
        "address": "пр. Острякова 65А",
        "cycle_length": 14,
        "anchor_date": ANCHOR,
        "notify_arrival": True,
        "notify_max_discount": True,
        "notify_middle": False,
    }
    base.update(overrides)
    return base


class TestPickEvents:
    def test_arrival_when_flag_on(self) -> None:
        assert pick_events_for_subscription(_sub(), ANCHOR, set()) == ["arrival"]

    def test_arrival_skipped_when_flag_off(self) -> None:
        assert pick_events_for_subscription(_sub(notify_arrival=False), ANCHOR, set()) == []

    def test_max_discount_picked(self) -> None:
        assert pick_events_for_subscription(_sub(), date(2026, 4, 14), set()) == ["max_discount"]

    def test_middle_skipped_when_flag_off(self) -> None:
        # day 7 = middle
        assert pick_events_for_subscription(_sub(), date(2026, 4, 8), set()) == []

    def test_middle_included_when_flag_on(self) -> None:
        sub = _sub(notify_middle=True)
        assert pick_events_for_subscription(sub, date(2026, 4, 8), set()) == ["middle"]

    def test_no_events_on_normal_day(self) -> None:
        assert pick_events_for_subscription(_sub(), date(2026, 4, 3), set()) == []

    def test_non_cycle_with_matching_weekday(self) -> None:
        sub = _sub(cycle_length=None, anchor_date=None)
        # 2026-04-29 is Wednesday (weekday=2)
        assert pick_events_for_subscription(sub, date(2026, 4, 29), {2}) == ["weekday"]

    def test_non_cycle_without_matching_weekday(self) -> None:
        sub = _sub(cycle_length=None, anchor_date=None)
        assert pick_events_for_subscription(sub, date(2026, 4, 29), {0, 6}) == []


class TestFormatMessage:
    def test_empty_returns_empty_string(self) -> None:
        assert format_message([]) == ""

    def test_single_trigger(self) -> None:
        t = Trigger(1, "Megahand", "пр. Острякова 65А", "arrival")
        msg = format_message([t])
        assert "🆕 День завоза" in msg
        assert "Megahand" in msg
        assert "пр. Острякова 65А" in msg

    def test_groups_by_event_type(self) -> None:
        triggers = [
            Trigger(1, "Megahand", "адрес 1", "arrival"),
            Trigger(2, "Favorite", "адрес 2", "arrival"),
            Trigger(3, "Shop3", "адрес 3", "max_discount"),
        ]
        msg = format_message(triggers)
        # arrival comes before max_discount per EVENT_ORDER
        assert msg.index("🆕") < msg.index("💰")
        assert msg.count("🆕") == 1  # one header
        assert "Megahand" in msg and "Favorite" in msg and "Shop3" in msg

    def test_html_escapes_shop_name(self) -> None:
        t = Trigger(1, "<script>", "адрес", "arrival")
        msg = format_message([t])
        assert "<script>" not in msg
        assert "&lt;script&gt;" in msg

    def test_weekday_event(self) -> None:
        t = Trigger(1, "Shop", "адрес", "weekday")
        msg = format_message([t])
        assert "📅 Напоминание" in msg
