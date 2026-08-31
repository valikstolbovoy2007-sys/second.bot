"""Shop card rendering.

The card markup is driven by an editable template stored in `bot_config`
(see `services/card_template.py`). This module just builds the context from
the shop and renders the template, with a hardcoded fallback if anything
goes wrong.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from data.repos.shops import Shop
from services.card_blocks import (
    CONFIG_KEY_BLOCKS,
    assemble as assemble_blocks,
    from_json as blocks_from_json,
)
from services.card_template import (
    CONFIG_KEY,
    DEFAULT_TEMPLATE,
    build_context,
    price_phase_emoji,
    render,
    validate,
)
from services.config_live import get as cfg_get
from services.cycle import (
    CycleInfo,
    EventType,
    day_in_cycle,
    events_on,
    next_event_date,
)

log = logging.getLogger(__name__)

EVENT_EMOJI: dict[EventType, str] = {
    EventType.ARRIVAL: "🆕",
    EventType.MAX_DISCOUNT: "💰",
    EventType.MIDDLE: "⚖️",
}

_MONTHS_RU = {
    1: "янв", 2: "фев", 3: "мар", 4: "апр", 5: "май", 6: "июн",
    7: "июл", 8: "авг", 9: "сен", 10: "окт", 11: "ноя", 12: "дек",
}
_WEEKDAY_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
_SEP = "━━━━━━━━━━━━━━━━"


def today_marker(shop: Shop, today: date) -> str:
    if shop.cycle_length is None or shop.anchor_date is None:
        return ""
    events = events_on(today, CycleInfo(shop.cycle_length, shop.anchor_date))
    if not events:
        return ""
    return EVENT_EMOJI[next(iter(events))]


def phase_marker(shop: Shop, today: date) -> str:
    """🚚 on arrival day, else 🔴/🟡/🟢 for how far into the price cycle today is.

    Empty if the shop has no cycle configured.
    """
    if shop.cycle_length is None or shop.anchor_date is None:
        return ""
    d = day_in_cycle(today, CycleInfo(shop.cycle_length, shop.anchor_date))
    return price_phase_emoji(d, shop.cycle_length)


async def _load_template() -> str:
    # 1. Manual HTML template (raw mode) wins if present and valid.
    raw = await cfg_get(CONFIG_KEY, None)
    if raw:
        ok, _err = validate(raw)
        if ok:
            return raw
        log.warning("invalid manual card template in bot_config, ignoring")
    # 2. Otherwise assemble from per-block configuration.
    blocks_raw = await cfg_get(CONFIG_KEY_BLOCKS, None)
    if blocks_raw:
        try:
            assembled = assemble_blocks(blocks_from_json(blocks_raw))
        except Exception:
            log.exception("failed to assemble shop_card_blocks, using default")
        else:
            ok, _err = validate(assembled)
            if ok:
                return assembled
            log.warning("assembled blocks failed validation, using default")
    # 3. Hardcoded fallback.
    return DEFAULT_TEMPLATE


async def format_shop_card(shop: Shop, today: date, is_tracked: bool = False) -> str:
    ctx = build_context(shop, today, is_tracked=is_tracked)
    tpl = await _load_template()
    try:
        return render(tpl, ctx)
    except Exception:
        log.exception("card render failed, falling back to default template")
        return render(DEFAULT_TEMPLATE, ctx)


def render_with_template(shop: Shop, today: date, template: str, *, is_tracked: bool = False) -> str:
    """Synchronous render with a given template — used for preview UI."""
    ctx = build_context(shop, today, is_tracked=is_tracked)
    return render(template, ctx)


def _fmt_price(p: int) -> str:
    return f"{p:,}".replace(",", " ")


def format_price_schedule(shop: Shop, today: date) -> str:
    """Day-by-day price drops from today until next arrival."""
    import html as _html
    from services.cycle import day_in_cycle, days_until

    header = (
        f"📋 <b>Расписание цен</b>\n"
        f"🏪 <b>{_html.escape(shop.name)}</b>\n"
        f"{_SEP}"
    )
    if not shop.price_start or shop.price_step is None:
        return (
            f"{header}\n\n"
            "💭 Цены пока не уточнили — но магазин работает.\n"
            "Загляни в карточку, чтобы посмотреть адрес и расписание завозов."
        )
    if shop.cycle_length is None or shop.anchor_date is None:
        return (
            f"{header}\n\n"
            "📅 Расписание завозов уточняется.\n"
            "Как только появится — всё подтянется автоматически."
        )

    info = CycleInfo(shop.cycle_length, shop.anchor_date)
    today_day = day_in_cycle(today, info)
    days_to_next = days_until(today, info, EventType.ARRIVAL)
    show_days = shop.cycle_length if days_to_next == 0 else days_to_next

    lines = [
        "📋 <b>Расписание цен</b>",
        f"🏪 <b>{_html.escape(shop.name)}</b>",
        _SEP,
        f"🗓 Цикл: {shop.cycle_length} дн. · шаг {_fmt_price(shop.price_step)} ₽/день",
        _SEP,
    ]

    for i in range(show_days + 1):
        d = today + timedelta(days=i)
        cycle_day = (today_day + i) % shop.cycle_length
        wd = _WEEKDAY_RU[d.weekday()]
        mon = _MONTHS_RU[d.month]
        date_str = f"{wd}, {d.day} {mon}"
        is_today = i == 0
        is_arrival = cycle_day == 0

        if is_arrival:
            tag = " · сегодня" if is_today else ""
            lines.append(
                f"<b>🚚 {date_str}  —  {_fmt_price(shop.price_start)} ₽  завоз{tag}</b>"
            )
        else:
            price = max(0, shop.price_start - cycle_day * shop.price_step)
            if is_today:
                lines.append(
                    f"<b>  ▶ {date_str}  —  {_fmt_price(price)} ₽  · сегодня</b>"
                )
            else:
                lines.append(f"      {date_str}  —  {_fmt_price(price)} ₽")

    return "\n".join(lines)
