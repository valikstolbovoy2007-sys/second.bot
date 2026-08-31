"""Editable shop-card template.

The template is a string stored in `bot_config` under key `shop_card_template`.
Two syntaxes are supported:

  {var}           -> substitutes the value of `var` from the context
  {?var:text}     -> renders `text` only if `var` is truthy (non-empty)
                     `text` may contain other {var} placeholders but no
                     nested {?...:} blocks. Use `{?!var:text}` to negate.

If the template is invalid or the bot_config key is unset, the default
template below is used.
"""
from __future__ import annotations

import html
import re
from datetime import date, timedelta
from typing import Any

from data.repos.shops import Shop
from services.cycle import (
    CycleInfo,
    EventType,
    day_in_cycle,
    days_until,
    events_on,
    humanize_days,
    next_event_date,
)

CONFIG_KEY = "shop_card_template"

_SEP2 = "_________________"
_SEP = "────────────"

_MONTHS_RU = {
    1: "янв", 2: "фев", 3: "мар", 4: "апр", 5: "май", 6: "июн",
    7: "июл", 8: "авг", 9: "сен", 10: "окт", 11: "ноя", 12: "дек",
}
_WEEKDAY_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

DEFAULT_TEMPLATE = """🏪 <b>{name}</b>
{?chain:<i>{chain}</i>
}
{?price_today_line:{price_today_line}
{?day_label:{day_label}
}}
{?next_arrival_date:<blockquote>🚚 Завоз: <b>{next_arrival_date}</b> - {days_to_arrival}{?price_start:
В день завоза <b>{price_start} ₽/кг</b>}{?today_event:
Сегодня: {today_event}}</blockquote>
}{?working_hours:<code>{working_hours}</code>}

📍 {address_link}
{?description:⚡ {description}}
{?is_tracked:

💘 <i>Ты отслеживаешь этот магазин</i>}"""


# Variables that the template author can use, with descriptions for the UI.
VARIABLES: list[tuple[str, str]] = [
    ("name", "Название магазина"),
    ("chain", "Сеть (или пусто)"),
    ("address", "Адрес (просто текст)"),
    ("address_link", "Кликабельный адрес-ссылка на Яндекс.Карты"),
    ("working_hours", "Время работы (или пусто)"),
    ("description", "Описание (или пусто)"),
    ("cycle", "Длина цикла, дн."),
    ("price_start", "Цена в день завоза, ₽/кг"),
    ("price_step", "Шаг снижения, ₽/день"),
    ("price_today", "Цена сегодня, ₽/кг (если можно посчитать)"),
    ("price_today_line", "Готовая строка «🟡 Цена сегодня: 850 ₽/кг» (или пусто)"),
    ("price_emoji", "Эмодзи фазы цикла: 🚚 / 🔴 / 🟡 / 🟢"),
    ("day_in_cycle", "День цикла (1, 2, ...)"),
    ("day_label", "«5-й день цикла» или «5-й день — самый дешёвый!»"),
    ("next_arrival_date", "Дата следующего завоза («Чт, 14 май»)"),
    ("days_to_arrival", "До завоза («через 5 дней» / «завтра» / «сегодня»)"),
    ("max_discount_date", "Дата максимальной скидки («13.05»)"),
    ("days_to_max_discount", "До макс. скидки («через 4 дня»)"),
    ("today_event", "Если сегодня завоз/середина/макс.скидка"),
    ("is_tracked", "Заполнено если пользователь отслеживает магазин"),
    ("sep", "Разделитель ━━━━━━━━━━━━━━━━"),
    ("sep2", "Разделитель ________________"),
]


def _fmt_price(p: int | None) -> str:
    if p is None:
        return ""
    return f"{p:,}".replace(",", " ")


def price_phase_emoji(day: int, cycle: int) -> str:
    """🚚 on arrival day, else 🔴/🟡/🟢 depending on how far into the cycle."""
    if day == 0:
        return "🚚"
    quarter = max(1, cycle // 4)
    if day <= quarter:
        return "🔴"
    if day >= cycle - quarter:
        return "🟢"
    return "🟡"


def build_context(shop: Shop, today: date, *, is_tracked: bool = False) -> dict[str, str]:
    """Compute the dict of values fed into the template."""
    ctx: dict[str, str] = {
        "name": html.escape(shop.name),
        "chain": html.escape(shop.chain_name) if shop.chain_name else "",
        "address": html.escape(shop.address),
        "address_link": (
            f'<a href="{html.escape(shop.maps_url, quote=True)}">'
            f'{html.escape(shop.address)}'
            f'</a>'
            if shop.maps_url
            else html.escape(shop.address)
        ),
        "working_hours": html.escape(shop.working_hours) if shop.working_hours else "",
        "description": html.escape(shop.description) if shop.description else "",
        "cycle": str(shop.cycle_length) if shop.cycle_length else "",
        "price_start": _fmt_price(shop.price_start) if shop.price_start else "",
        "price_step": str(shop.price_step) if shop.price_step is not None else "",
        "price_today": "",
        "price_today_line": "",
        "price_emoji": "",
        "day_in_cycle": "",
        "day_label": "",
        "next_arrival_date": "",
        "days_to_arrival": "",
        "max_discount_date": "",
        "days_to_max_discount": "",
        "today_event": "",
        "is_tracked": "1" if is_tracked else "",
        "sep": _SEP,
        "sep2": _SEP2,
    }

    if shop.cycle_length and shop.anchor_date:
        info = CycleInfo(shop.cycle_length, shop.anchor_date)
        d = day_in_cycle(today, info)
        ctx["day_in_cycle"] = str(d + 1)

        if shop.price_start and shop.price_step is not None:
            price = max(0, shop.price_start - max(0, d) * shop.price_step)
            emoji = price_phase_emoji(d, shop.cycle_length)
            ctx["price_today"] = _fmt_price(price)
            ctx["price_emoji"] = emoji
            if d == 0:
                ctx["price_today_line"] = (
                    f"🚚 Цена сегодня\n<b>{_fmt_price(price)} ₽/кг</b> — завоз!"
                )
                ctx["day_label"] = ""
            elif price > 0:
                ctx["price_today_line"] = (
                    f"{emoji} Цена сегодня\n<b>{_fmt_price(price)} ₽/кг</b>"
                )
                ctx["day_label"] = f"<b>{d + 1}-й день</b>"
            else:
                ctx["price_today_line"] = "🟢 Цена сегодня\n<b>~0 ₽/кг</b> 🔥"
                ctx["day_label"] = f"<b>{d + 1}-й день — самый дешёвый!</b>"

        nxt = next_event_date(today, info, EventType.ARRIVAL)
        ctx["next_arrival_date"] = (
            f"{_WEEKDAY_RU[nxt.weekday()]}, {nxt.day} {_MONTHS_RU[nxt.month]}"
        )
        ctx["days_to_arrival"] = humanize_days(days_until(today, info, EventType.ARRIVAL))

        md = next_event_date(today, info, EventType.MAX_DISCOUNT)
        ctx["max_discount_date"] = md.strftime("%d.%m")
        ctx["days_to_max_discount"] = humanize_days(
            days_until(today, info, EventType.MAX_DISCOUNT)
        )

        events = events_on(today, info)
        if events:
            ev_label = {
                EventType.ARRIVAL: "🆕 День завоза",
                EventType.MAX_DISCOUNT: "💰 Максимальная скидка",
                EventType.MIDDLE: "⚖️ Середина цикла",
            }
            ctx["today_event"] = " · ".join(ev_label[e] for e in events)

    return ctx


# ---------- mini template engine ----------

#_VAR_RE = re.compile(r"\{([a-z_]+)\}")
#_VAR_RE = re.compile(r"{([a-z0-9*]+)}")
#_VAR_RE = re.compile(r"{([a-z0-9_*]+)}")
_VAR_RE = re.compile(r"\{([a-z0-9_*]+)\}")

_COND_HEAD_RE = re.compile(r"\{\?(!?)([a-z0-9_*]+):")
# Conditional head: `{?var:` or `{?!var:`. Body extends to the matching
# closing brace via manual brace-balance scanning (regex `[^{}]*` was too
# restrictive — bodies often contain nested `{var}` placeholders).
#_COND_HEAD_RE = re.compile(r"\{\?(!?)([a-z_]+):")
#_COND_HEAD_RE = re.compile(r"{?(!?)([a-z0-9_*]+):")
_MAX_TEMPLATE_LEN = 10000  # guard against runaway recursion


def _resolve_conditionals(template: str, ctx: dict[str, str], depth: int = 0) -> str:
    """Resolve every `{?var:body}` / `{?!var:body}` block in `template`.

    Bodies may contain nested conditionals (handled by recursion) and
    `{var}` placeholders (left untouched — final substitution happens
    once, after all conditionals are resolved). Malformed (unclosed)
    conditionals are kept verbatim.
    """
    if depth > 16 or len(template) > _MAX_TEMPLATE_LEN:
        return template
    out: list[str] = []
    i = 0
    n = len(template)
    while i < n:
        m = _COND_HEAD_RE.match(template, i)
        if not m:
            out.append(template[i])
            i += 1
            continue
        # Scan forward for the matching `}` with brace balancing.
        body_start = m.end()
        j = body_start
        depth_braces = 1
        while j < n and depth_braces > 0:
            ch = template[j]
            if ch == "{":
                depth_braces += 1
            elif ch == "}":
                depth_braces -= 1
            j += 1
        if depth_braces != 0:
            # Unbalanced — keep verbatim and move past `{?` to avoid infinite loop.
            out.append(template[i])
            i += 1
            continue
        body = template[body_start : j - 1]
        negate = m.group(1) == "!"
        truthy = bool(ctx.get(m.group(2)))
        if negate:
            truthy = not truthy
        if truthy:
            out.append(_resolve_conditionals(body, ctx, depth + 1))
        # else: drop the block entirely
        i = j
    return "".join(out)


def render(template: str, ctx: dict[str, str]) -> str:
    # 1) Resolve all conditional blocks (may contain `{var}` placeholders
    #    inside bodies — these are NOT substituted yet).
    out = _resolve_conditionals(template, ctx)
    # 2) Substitute `{var}` placeholders in a single pass. This happens
    #    after all conditional decisions are made — so user-controlled
    #    values (e.g. a shop name containing literal `{?chain:HACK}`) cannot
    #    create new conditional blocks that get evaluated.
    out = _VAR_RE.sub(lambda m: ctx.get(m.group(1), ""), out)
    # 3) Collapse blank-line artifacts from removed conditional blocks.
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip("\n")


def validate(template: str) -> tuple[bool, str]:
    """Returns (ok, error_message). Tries to render with a sample shop."""
    if len(template) > 4000:
        return False, "Шаблон слишком длинный (>4000 символов)."
    if not template.strip():
        return False, "Шаблон пустой."
    sample = Shop(
        id=0, name="Тест", address="Тестовая, 1", description="Описание",
        chain_name="Megahand", cycle_length=14,
        anchor_date=date.today() - timedelta(days=3),
        price_start=1200, price_step=50,
        working_hours="Пн-Сб 10:00–21:00, Вс выходной",
        is_active=True,
        maps_url=None,
    )
    try:
        ctx = build_context(sample, date.today(), is_tracked=False)
        render(template, ctx)
    except Exception as exc:
        return False, f"Ошибка рендера: {exc!s}"
    return True, ""
