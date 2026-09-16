import asyncio
from datetime import date, datetime, time
from unittest.mock import AsyncMock, patch

from services.notifier import (
    ARRIVAL_LEAD_DAYS,
    CHEAP_DAY_LEAD_DAYS,
    MIDDLE_LEAD_DAYS,
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
        "notify_middle": True,
    }
    base.update(overrides)
    return base


class TestFormatShopMessage:
    def setup_method(self) -> None:
        set_bot_username("Shmot92Bot")

    def teardown_method(self) -> None:
        set_bot_username(None)

    def test_single_trigger(self) -> None:
        trig = Trigger(1, "Megahand", "пр. Острякова 65А", "arrival", lead_days=0)
        msg = format_shop_message(trig)
        assert "🚚 Завтра день завоза" in msg
        assert "Megahand" in msg
        assert "пр. Острякова 65А" in msg

    def test_cheap_day_notice_mentions_weekday(self) -> None:
        trig = Trigger(1, "Megahand", "адрес", "cheap_day", lead_days=1,
                       arrival_weekday="Четверг")
        msg = format_shop_message(trig)
        assert "💰 Завтра самый дешёвый день" in msg
        assert "завоз в Четверг" in msg

    def test_middle_cycle_message(self) -> None:
        trig = Trigger(1, "Megahand", "адрес", "middle", lead_days=0)
        msg = format_shop_message(trig)
        assert "⚖️ Сегодня середина цикла" in msg

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

    def test_arrival_fires_on_arrival_day_at_9am(self) -> None:
        # anchor Apr 1, cycle 14 -> arrival Apr 15; fires that very day.
        row = _row(notify_cheap_day=False)
        with patch("services.notifier.fetch_notify_candidates", new=AsyncMock(return_value=[row])), \
             patch("services.notifier.already_sent", new=AsyncMock(return_value=set())), \
             patch("services.notifier.mark_sent", new=AsyncMock()) as mark_sent_mock:
            bot = AsyncMock()
            when = datetime.combine(date(2026, 4, 15), NOTIFY_AT)
            asyncio.run(run_for_minute(bot, when))
            bot.send_message.assert_called_once()
            tg_id, text = bot.send_message.call_args[0]
            assert tg_id == 555
            assert "🚚 Завтра день завоза" in text
            mark_sent_mock.assert_called_once_with(1, [(1, "arrival")], date(2026, 4, 15))

    def test_cheap_day_fires_tow_days_before_arrival_at_9am(self) -> None:
        # max_discount day = last day of cycle = Apr 14 (day 13 of 0..13).
        # The notification fires one day before that, i.e. Apr 13 — that is
        # two days before the Apr 15 arrival.
        row = _row(notify_arrival=False)
        with patch("services.notifier.fetch_notify_candidates", new=AsyncMock(return_value=[row])), \
             patch("services.notifier.already_sent", new=AsyncMock(return_value=set())), \
             patch("services.notifier.mark_sent", new=AsyncMock()) as mark_sent_mock:
            bot = AsyncMock()
            when = datetime.combine(date(2026, 4, 13), NOTIFY_AT)
            asyncio.run(run_for_minute(bot, when))
            bot.send_message.assert_called_once()
            _tg_id, text = bot.send_message.call_args[0]
            assert "💰 Завтра самый дешёвый день" in text
            # arrival Apr 15 2026 is a Wednesday.
            assert "завоз в Среда" in text
            mark_sent_mock.assert_called_once_with(1, [(1, "cheap_day")], date(2026, 4, 13))

    def test_two_shops_same_user_send_two_messages(self) -> None:
        row_a = _row(user_id=1, tg_id=555, shop_id=1, name="Shop A", notify_cheap_day=False)
        row_b = _row(user_id=1, tg_id=555, shop_id=2, name="Shop B", notify_cheap_day=False)
        with patch("services.notifier.fetch_notify_candidates",
                   new=AsyncMock(return_value=[row_a, row_b])), \
             patch("services.notifier.already_sent", new=AsyncMock(return_value=set())), \
             patch("services.notifier.mark_sent", new=AsyncMock()) as mark_sent_mock:
            bot = AsyncMock()
            when = datetime.combine(date(2026, 4, 15), NOTIFY_AT)
            asyncio.run(run_for_minute(bot, when))
            assert bot.send_message.call_count == 2
            texts = [c.args[1] for c in bot.send_message.call_args_list]
            assert any("Shop A" in t for t in texts)
            assert any("Shop B" in t for t in texts)
            # Каждое (shop_id, event_type) маркируется отдельно.
            mark_sent_mock.assert_called_once_with(
                1, [(1, "arrival"), (2, "arrival")], date(2026, 4, 15),
            )

    def test_middle_fires_on_middle_day_at_9am(self) -> None:
        # cycle 14 → middle = day 7 after anchor Apr 1 = Apr 8.
        row = _row(notify_arrival=False, notify_cheap_day=False)
        with patch("services.notifier.fetch_notify_candidates", new=AsyncMock(return_value=[row])), \
             patch("services.notifier.already_sent", new=AsyncMock(return_value=set())), \
             patch("services.notifier.mark_sent", new=AsyncMock()) as mark_sent_mock:
            bot = AsyncMock()
            when = datetime.combine(date(2026, 4, 8), NOTIFY_AT)
            asyncio.run(run_for_minute(bot, when))
            bot.send_message.assert_called_once()
            _tg_id, text = bot.send_message.call_args[0]
            assert "⚖️ Сегодня середина цикла" in text
            mark_sent_mock.assert_called_once_with(1, [(1, "middle")], date(2026, 4, 8))

    def test_middle_off_by_default_not_fired(self) -> None:
        row = _row(notify_arrival=False, notify_cheap_day=False, notify_middle=False)
        with patch("services.notifier.fetch_notify_candidates", new=AsyncMock(return_value=[row])), \
             patch("services.notifier.already_sent", new=AsyncMock(return_value=set())), \
             patch("services.notifier.mark_sent", new=AsyncMock()):
            bot = AsyncMock()
            when = datetime.combine(date(2026, 4, 8), NOTIFY_AT)
            asyncio.run(run_for_minute(bot, when))
            bot.send_message.assert_not_called()

    def test_no_cycle_shop_produces_no_trigger(self) -> None:
        row = _row(cycle_length=None, anchor_date=None)
        with patch("services.notifier.fetch_notify_candidates", new=AsyncMock(return_value=[row])), \
             patch("services.notifier.already_sent", new=AsyncMock(return_value=set())), \
             patch("services.notifier.mark_sent", new=AsyncMock()):
            bot = AsyncMock()
            when = datetime.combine(date(2026, 4, 15), NOTIFY_AT)
            asyncio.run(run_for_minute(bot, when))
            bot.send_message.assert_not_called()

    def test_already_sent_is_skipped(self) -> None:
        row = _row(notify_cheap_day=False)
        with patch("services.notifier.fetch_notify_candidates", new=AsyncMock(return_value=[row])), \
             patch("services.notifier.already_sent", new=AsyncMock(return_value={(1, "arrival")})), \
             patch("services.notifier.mark_sent", new=AsyncMock()) as mark_sent_mock:
            bot = AsyncMock()
            when = datetime.combine(date(2026, 4, 15), NOTIFY_AT)
            asyncio.run(run_for_minute(bot, when))
            bot.send_message.assert_not_called()
            mark_sent_mock.assert_not_called()


def test_lead_day_constants_match_spec() -> None:
    assert ARRIVAL_LEAD_DAYS == 0
    assert CHEAP_DAY_LEAD_DAYS == 1
    assert MIDDLE_LEAD_DAYS == 0
    assert NOTIFY_AT == time(9, 0)