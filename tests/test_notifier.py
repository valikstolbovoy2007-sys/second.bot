import asyncio
from datetime import date, datetime, time
from unittest.mock import AsyncMock, patch

from services.notifier import (
    ARRIVAL_LEAD_DAYS,
    CHEAP_DAY_LEAD_DAYS,
    NOTIFY_AT,
    Trigger,
    format_shop_message,
    run_for_minute,
    set_bot_username,
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


class TestFormatShopMessage:
    def setup_method(self) -> None:
        set_bot_username("Shmot92Bot")

    def teardown_method(self) -> None:
        set_bot_username(None)

    def test_single_trigger(self) -> None:
        trig = Trigger(1, "Megahand", "пр. Острякова 65А", "arrival", lead_days=1)
        msg = format_shop_message(trig)
        assert "🚚 Завоз" in msg
        assert "Megahand" in msg
        assert "пр. Острякова 65А" in msg
        assert "завтра" in msg

    def test_cheap_day_lead_today(self) -> None:
        trig = Trigger(1, "Megahand", "адрес", "cheap_day", lead_days=0)
        msg = format_shop_message(trig)
        assert "💰 Дешёвый день" in msg
        assert "сегодня" in msg

    def test_html_escapes_shop_name(self) -> None:
        trig = Trigger(1, "<script>", "адрес", "arrival", lead_days=1)
        msg = format_shop_message(trig)
        assert "<script>" not in msg
        assert "&lt;script&gt;" in msg

    def test_deep_link_uses_bot_username_and_shop_id(self) -> None:
        trig = Trigger(42, "Shop", "адрес", "arrival", lead_days=1)
        msg = format_shop_message(trig)
        assert "https://t.me/Shmot92Bot?start=shop_42" in msg
        assert "🔗 Открыть карточку" in msg

    def test_no_username_no_link(self) -> None:
        set_bot_username(None)
        trig = Trigger(1, "Shop", "адрес", "arrival", lead_days=1)
        msg = format_shop_message(trig)
        assert "Открыть карточку" not in msg
        assert "t.me/" not in msg


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

    def test_two_shops_same_user_send_two_messages(self) -> None:
        row_a = _row(user_id=1, tg_id=555, shop_id=1, name="Shop A", notify_cheap_day=False)
        row_b = _row(user_id=1, tg_id=555, shop_id=2, name="Shop B", notify_cheap_day=False)
        with patch("services.notifier.fetch_notify_candidates",
                   new=AsyncMock(return_value=[row_a, row_b])), \
             patch("services.notifier.already_sent", new=AsyncMock(return_value=set())), \
             patch("services.notifier.mark_sent", new=AsyncMock()) as mark_sent_mock:
            bot = AsyncMock()
            when = datetime.combine(date(2026, 4, 14), NOTIFY_AT)
            asyncio.run(run_for_minute(bot, when))
            assert bot.send_message.call_count == 2
            texts = [c.args[1] for c in bot.send_message.call_args_list]
            assert any("Shop A" in t for t in texts)
            assert any("Shop B" in t for t in texts)
            # Каждое (shop_id, event_type) маркируется отдельно.
            mark_sent_mock.assert_called_once_with(
                1, [(1, "arrival"), (2, "arrival")], date(2026, 4, 14),
            )

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