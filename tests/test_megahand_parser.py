from datetime import date

from services.megahand_parser import parse_arrival


class TestParseArrival:
    def test_data_date_attribute(self) -> None:
        html = '<div data-date="2026-04-15">Новый завоз</div>'
        assert parse_arrival(html, today=date(2026, 4, 20)) == date(2026, 4, 15)

    def test_text_with_dot_date(self) -> None:
        html = '<span>15.04.2026 Новый завоз</span>'
        assert parse_arrival(html, today=date(2026, 4, 20)) == date(2026, 4, 15)

    def test_russian_month_inherits_year(self) -> None:
        html = '<div>15 апреля Новый завоз</div>'
        assert parse_arrival(html, today=date(2026, 4, 20)) == date(2026, 4, 15)

    def test_picks_most_recent_past(self) -> None:
        html = (
            '<div data-date="2026-04-01">Новый завоз</div>'
            '<div data-date="2026-04-15">Новый завоз</div>'
            '<div data-date="2026-04-29">Новый завоз</div>'
        )
        assert parse_arrival(html, today=date(2026, 4, 20)) == date(2026, 4, 15)

    def test_picks_nearest_future_when_no_past(self) -> None:
        html = (
            '<div data-date="2026-05-01">Новый завоз</div>'
            '<div data-date="2026-05-15">Новый завоз</div>'
        )
        assert parse_arrival(html, today=date(2026, 4, 20)) == date(2026, 5, 1)

    def test_returns_none_when_no_arrival_marker(self) -> None:
        html = '<div data-date="2026-04-15">Скидка 30%</div>'
        assert parse_arrival(html, today=date(2026, 4, 20)) is None

    def test_returns_none_on_empty_html(self) -> None:
        assert parse_arrival("", today=date(2026, 4, 20)) is None

    def test_uses_parent_text_for_date(self) -> None:
        html = (
            '<div data-day="2026-04-10">'
            '<span class="badge">Новый завоз</span>'
            '<span class="num">10</span>'
            "</div>"
        )
        assert parse_arrival(html, today=date(2026, 4, 20)) == date(2026, 4, 10)
