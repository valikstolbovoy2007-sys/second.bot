import asyncio
from datetime import date, datetime, time
from unittest.mock import AsyncMock, patch

from services.notifier import (
    ARRIVAL_LEAD_DAYS,
    CHEAP_DAY_LEAD_DAYS,
    NOTIFY_AT,
    Trigger,
    format_message,
    run_for_minute,
)

ANCHOR = date(2026, 4, 1)  # cycle_length=14 -> arrival on Apr 1, 15, 29...


def _row(**overrides) -> dict:
    base = {
        "user_id": 1,
        "tg_id": 555,
        "shop_id": 1,
        "name": "Megahand",
        "address": "пр. Острякова 65А",
        "cycle_length": 14,
        "anchor_date": ANCHOR,
        "monthly_weekday": None,
        "monthly_occurrence": 1,
        "notify_arrival": True,
        "notify_cheap_day": True,
    }
    base.update(overrides)
    return base


class TestFormatMessage:
    def test_empty_returns_empty_string(self) -> None:
        assert format_message([]) == ""

    def test_single_trigger(self) -> None:
        trig = Trigger(1, "Megahand", "пр. Острякова 65А", "arrival", lead_days=1)
        msg = format_message([trig])
        assert "🚚 Завоз" in msg
        assert "Megahand" in msg
        assert "пр. Острякова 65А" in msg
        assert "завтра" in msg

    def test_groups_by_event_type(self) -> None:
        triggers = [
            Trigger(1, "Megahand", "адрес 1", "arrival", lead_days=1),
            Trigger(2, "Favorite", "адрес 2", "arrival", lead_days=1),
            Trigger(3, "Shop3", "адрес 3", "cheap_day", lead_days=0),
        ]
        msg = format_message(triggers)
        # arrival comes before cheap_day per EVENT_ORDER
        assert msg.index("🚚") < msg.index("💰")
        assert msg.count("🚚") == 1  # one header
        assert "Megahand" in msg and "Favorite" in msg and "Shop3" in msg

    def test_html_escapes_shop_name(self) -> None:
        trig = Trigger(1, "<script>", "адрес", "arrival", lead_days=1)
        msg = format_message([trig])
        assert "<script>" not in msg
        assert "&lt;script&gt;" in msg


class TestRunForMinute:
    def test_skips_off_schedule_ticks(self) -> None:
        with patch("services.notifier.fetch_notify_candidates", new=AsyncMock()) as fetch_mock:
            bot = AsyncMock()
            when = datetime.combine(date(2026, 4, 14), time(10, 0))
            asyncio.run(run_for_minute(bot, when))
            fetch_mock.assert_not_called()
            bot.send_message.assert_not_called()

    def test_arrival_fires_one_day_before_at_9am(self) -> None:
        # anchor Apr 1, cycle 14 -> arrival Apr 15; one day before = Apr 14.
        row = _row(notify_cheap_day=False)
        with patch("services.notifier.fetch_notify_candidates", new=AsyncMock(return_value=[row])), \
             patch("services.notifier.already_sent", new=AsyncMock(return_value=set())), \
             patch("services.notifier.mark_sent", new=AsyncMock()) as mark_sent_mock:
            bot = AsyncMock()
            when = datetime.combine(date(2026, 4, 14), NOTIFY_AT)
            asyncio.run(run_for_minute(bot, when))
            bot.send_message.assert_called_once()
            tg_id, text = bot.send_message.call_args[0]
            assert tg_id == 555
            assert "🚚 Завоз" in text
            mark_sent_mock.assert_called_once_with(1, [(1, "arrival")], date(2026, 4, 14))

    def test_cheap_day_fires_same_day_at_9am(self) -> None:
        # max_discount day = last day of cycle = Apr 14 (day 13 of 0..13).
        row = _row(notify_arrival=False)
        with patch("services.notifier.fetch_notify_candidates", new=AsyncMock(return_value=[row])), \
             patch("services.notifier.already_sent", new=AsyncMock(return_value=set())), \
             patch("services.notifier.mark_sent", new=AsyncMock()) as mark_sent_mock:
            bot = AsyncMock()
            when = datetime.combine(date(2026, 4, 14), NOTIFY_AT)
            asyncio.run(run_for_minute(bot, when))
            bot.send_message.assert_called_once()
            _tg_id, text = bot.send_message.call_args[0]
            assert "💰 Дешёвый день" in text
            mark_sent_mock.assert_called_once_with(1, [(1, "cheap_day")], date(2026, 4, 14))

    def test_no_cycle_shop_produces_no_trigger(self) -> None:
        row = _row(cycle_length=None, anchor_date=None)
        with patch("services.notifier.fetch_notify_candidates", new=AsyncMock(return_value=[row])), \
             patch("services.notifier.already_sent", new=AsyncMock(return_value=set())), \
             patch("services.notifier.mark_sent", new=AsyncMock()):
            bot = AsyncMock()
            when = datetime.combine(date(2026, 4, 14), NOTIFY_AT)
            asyncio.run(run_for_minute(bot, when))
            bot.send_message.assert_not_called()

    def test_already_sent_is_skipped(self) -> None:
        row = _row(notify_cheap_day=False)
        with patch("services.notifier.fetch_notify_candidates", new=AsyncMock(return_value=[row])), \
             patch("services.notifier.already_sent", new=AsyncMock(return_value={(1, "arrival")})), \
             patch("services.notifier.mark_sent", new=AsyncMock()) as mark_sent_mock:
            bot = AsyncMock()
            when = datetime.combine(date(2026, 4, 14), NOTIFY_AT)
            asyncio.run(run_for_minute(bot, when))
            bot.send_message.assert_not_called()
            mark_sent_mock.assert_not_called()


def test_lead_day_constants_match_spec() -> None:
    assert ARRIVAL_LEAD_DAYS == 1
    assert CHEAP_DAY_LEAD_DAYS == 0
    assert NOTIFY_AT == time(9, 0)
