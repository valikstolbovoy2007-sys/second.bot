import logging
import re
from datetime import date

import httpx
from selectolax.parser import HTMLParser

log = logging.getLogger(__name__)

URL = "https://sevastopol.mhand.ru/promo/"

MONTHS_RU: dict[str, int] = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
    "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}

ISO_DATE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
DOT_DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{2,4})\b")
RU_DATE = re.compile(r"\b(\d{1,2})\s+(" + "|".join(MONTHS_RU) + r")\b", re.I)
ARRIVAL_PHRASE = re.compile(r"новый\s*завоз", re.I)


async def fetch_megahand_arrival() -> date | None:
    async with httpx.AsyncClient(
        timeout=15.0,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (compatible; secondbot)"},
    ) as client:
        resp = await client.get(URL)
        resp.raise_for_status()
    return parse_arrival(resp.text, today=date.today())


def parse_arrival(html: str, today: date) -> date | None:
    candidates = _extract_arrival_dates(html, today.year)
    if not candidates:
        return None
    past = [d for d in candidates if d <= today]
    if past:
        return max(past)
    return min(candidates)


def _extract_arrival_dates(html: str, year: int) -> list[date]:
    tree = HTMLParser(html)
    found: set[date] = set()

    for node in tree.css("*"):
        text = node.text(separator=" ", strip=True) or ""
        if not ARRIVAL_PHRASE.search(text):
            continue

        for attr in ("data-date", "data-day", "datetime"):
            val = node.attributes.get(attr) if node.attributes else None
            if val and (d := _parse_iso(val)):
                found.add(d)

        if d := _find_date_in_text(text, year):
            found.add(d)

        parent = node.parent
        if parent:
            ptext = parent.text(separator=" ", strip=True) or ""
            if d := _find_date_in_text(ptext, year):
                found.add(d)

    return sorted(found)


def _parse_iso(s: str) -> date | None:
    m = ISO_DATE.match(s.strip())
    if not m:
        return None
    try:
        return date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return None


def _find_date_in_text(text: str, year: int) -> date | None:
    if m := DOT_DATE.search(text):
        d, mo, y = int(m[1]), int(m[2]), int(m[3])
        if y < 100:
            y += 2000
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    if m := ISO_DATE.search(text):
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    if m := RU_DATE.search(text):
        try:
            return date(year, MONTHS_RU[m[2].lower()], int(m[1]))
        except ValueError:
            return None
    return None
