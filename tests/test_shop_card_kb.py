"""Кнопка «❓ Задать вопрос сешке» на карточке магазина."""
from datetime import date

from keyboards.catalog_kb import CatalogCb, shop_card_kb
from data.repos.shops import Shop


def _shop(**overrides) -> Shop:
    base = dict(
        id=3, name="Сешка", address="ул. Тестовая 1", description=None, chain_name=None,
        cycle_length=14, anchor_date=date(2026, 4, 1), price_start=1200, price_step=80,
        working_hours=None, is_active=True, maps_url=None, monthly_weekday=None,
        monthly_occurrence=1,
    )
    base.update(overrides)
    return Shop(**base)


def _buttons(kb):
    return [btn for row in kb.inline_keyboard for btn in row]


def _question_buttons(kb):
    out = []
    for btn in _buttons(kb):
        if not btn.callback_data or not btn.callback_data.startswith("cat:"):
            continue
        cb = CatalogCb.unpack(btn.callback_data)
        if cb.action == "ask":
            out.append((btn, cb))
    return out


def test_question_button_hidden_without_admin():
    kb = shop_card_kb(3, False, src="cat", page=0)
    assert _question_buttons(kb) == []


def test_question_button_shown_with_admin():
    kb = shop_card_kb(3, False, src="cat", page=0, has_admin=True)
    found = _question_buttons(kb)
    assert len(found) == 1
    btn, cb = found[0]
    assert btn.text == "❓ Задать вопрос сешке"
    assert cb.shop_id == 3
    assert cb.src == "cat"


def test_question_button_keeps_nav_fields():
    kb = shop_card_kb(7, True, src="my", page=2, flt="tracked", sort="price", has_admin=True)
    _, cb = _question_buttons(kb)[0]
    assert (cb.shop_id, cb.src, cb.page, cb.flt, cb.sort) == (7, "my", 2, "tracked", "price")
