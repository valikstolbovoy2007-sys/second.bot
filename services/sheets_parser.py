"""Google Sheets parser.

Reads a public Google Sheet (CSV export) and imports/updates shops in the DB.
The sheet URL is configurable via `bot_config` key `parser.sheets.url`; the
default points to the project's master sheet.

Sheet columns (Russian, see source sheet):
    Название, Актуальность, Популярность, Стартовая цена, Снижение в день,
    Режим работы, Якорная дата, Номер недели в месяце, День завоза,
    Частота завоза, Описание завоза, vk, Особенность, url, photo, active

Mapping into the `shops` table:
    name         <- Название
    chain_name   <- prefix of Название before "(" (best-effort)
    address      <- Yandex Maps URL (`url` column) — placeholder until address
                    can be entered manually
    cycle_length <- parsed from Частота завоза
    anchor_date  <- Якорная дата (YYYY-MM-DD)
    is_active    <- active == 1
    description  <- aggregated info block (hours, price, vk, special, etc.)

Upsert key: case-insensitive trimmed `name`.
"""
import csv
import io
import logging
import re
from datetime import date, datetime
from typing import Any

import httpx

from data.db import pool
from services.config_live import get as cfg_get

log = logging.getLogger(__name__)

DEFAULT_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1nPlgjbqNwreIz6Pq6iNFXeV8i5g3yJu45ocY2hqo8wA/export?format=csv&gid=0"
)

_FREQ_DAYS = {
    "еженедельно": 7,
    "раз в неделю": 7,
    "раз в 2 недели": 14,
    "раз в две недели": 14,
    "раз в 3 недели": 21,
    "раз в три недели": 21,
    "раз в 4 недели": 28,
    "раз в месяц": 30,
}

_PLACEHOLDER_RE = re.compile(r"^[-\s]+$")


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _is_placeholder(s: str) -> bool:
    return not s or bool(_PLACEHOLDER_RE.match(s))


def _clean(s: str | None) -> str:
    n = _norm(s)
    return "" if _is_placeholder(n) else n


def _parse_freq(s: str) -> int | None:
    n = _clean(s).lower()
    if not n:
        return None
    for k, days in _FREQ_DAYS.items():
        if k in n:
            return days
    if m := re.search(r"раз в (\d+)\s*недел", n):
        return int(m.group(1)) * 7
    return None


def _parse_anchor(s: str) -> date | None:
    n = _clean(s)
    if not n:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(n, fmt).date()
        except ValueError:
            continue
    return None


def _parse_active(s: str) -> bool:
    return _clean(s) == "1"


def _split_chain(name: str) -> tuple[str, str | None]:
    """Best-effort split: "Megahand (Океан)" -> ("Megahand (Океан)", "Megahand").

    Keep the full name as the shop name (it's the human label); extract the
    chain as the prefix before the first "(".
    """
    n = _norm(name)
    if "(" in n:
        prefix = n.split("(", 1)[0].strip()
        if 2 <= len(prefix) <= 40:
            return n, prefix
    first_word = n.split(" ", 1)[0]
    if first_word and first_word[0].isalpha() and len(first_word) >= 3:
        return n, first_word
    return n, None


def _build_description(row: dict[str, str]) -> str:
    parts: list[str] = []
    hours = _clean(row.get("Режим работы"))
    if hours:
        parts.append(f"🕘 {hours}")
    price = _clean(row.get("Стартовая цена"))
    reduction = _clean(row.get("Снижение в день"))
    if price or reduction:
        bits = []
        if price:
            bits.append(f"старт {price}₽")
        if reduction:
            bits.append(f"−{reduction}₽/день")
        parts.append("💰 " + ", ".join(bits))
    freq = _clean(row.get("Частота завоза"))
    if freq:
        parts.append(f"🔄 {freq}")
    arr_desc = _clean(row.get("Описание завоза"))
    if arr_desc:
        parts.append(f"📦 {arr_desc}")
    feat = _clean(row.get("Особенность"))
    if feat:
        parts.append(f"⭐ {feat}")
    vk = _clean(row.get("vk"))
    if vk:
        parts.append(f"VK: {vk}")
    photo = _clean(row.get("photo"))
    if photo:
        parts.append(f"📷 {photo}")
    return "\n".join(parts) or ""


def parse_csv(raw: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(raw))
    rows: list[dict[str, str]] = []
    for r in reader:
        clean = {(k or "").strip(): (v or "").strip() for k, v in r.items() if k}
        if not _norm(clean.get("Название", "")):
            continue
        rows.append(clean)
    return rows


async def fetch_csv(url: str) -> str:
    async with httpx.AsyncClient(
        timeout=30.0, follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (compatible; secondbot)"},
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
    return resp.text


def normalize_row(row: dict[str, str]) -> dict[str, Any]:
    raw_name = _norm(row.get("Название", ""))
    name, chain = _split_chain(raw_name)
    return {
        "name": name,
        "chain_name": chain,
        "address": _clean(row.get("url")) or "—",
        "cycle_length": _parse_freq(row.get("Частота завоза", "")),
        "anchor_date": _parse_anchor(row.get("Якорная дата", "")),
        "is_active": _parse_active(row.get("active", "")),
        "description": _build_description(row),
    }


async def import_sheets(*, apply: bool, actor_tg_id: int | None) -> dict[str, Any]:
    """Fetch, parse, and (optionally) upsert. Returns a stats dict."""
    url = await cfg_get("parser.sheets.url", DEFAULT_URL)
    raw = await fetch_csv(str(url))
    rows = parse_csv(raw)
    parsed = [normalize_row(r) for r in rows]

    stats: dict[str, Any] = {
        "rows": len(rows),
        "shops_new": 0,
        "shops_updated": 0,
        "arrivals_added": 0,
        "skipped": 0,
        "errors": [],
    }
    if not parsed:
        return stats

    if not apply:
        # Dry-run: just count what would change.
        async with pool().acquire() as conn:
            for p in parsed:
                ex = await conn.fetchrow(
                    "SELECT id FROM shops WHERE lower(trim(name)) = lower(trim($1))",
                    p["name"],
                )
                if ex:
                    stats["shops_updated"] += 1
                else:
                    stats["shops_new"] += 1
        stats["dry_run"] = True
        return stats

    async with pool().acquire() as conn:
        for p in parsed:
            try:
                ex = await conn.fetchrow(
                    "SELECT id, anchor_date FROM shops WHERE lower(trim(name)) = lower(trim($1))",
                    p["name"],
                )
                if ex:
                    sid = int(ex["id"])
                    await conn.execute(
                        """
                        UPDATE shops
                        SET chain_name = COALESCE($2, chain_name),
                            address     = CASE WHEN address = '—' OR address = '' THEN $3 ELSE address END,
                            cycle_length = COALESCE($4, cycle_length),
                            anchor_date  = COALESCE($5, anchor_date),
                            is_active    = $6,
                            description  = CASE WHEN description IS NULL OR description = '' THEN $7 ELSE description END
                        WHERE id = $1
                        """,
                        sid, p["chain_name"], p["address"], p["cycle_length"],
                        p["anchor_date"], p["is_active"], p["description"],
                    )
                    stats["shops_updated"] += 1
                    if p["anchor_date"] and p["anchor_date"] != ex["anchor_date"]:
                        await conn.execute(
                            "INSERT INTO arrivals (shop_id, arrival_date, source, set_by) "
                            "VALUES ($1, $2, 'sheet', $3)",
                            sid, p["anchor_date"], actor_tg_id,
                        )
                        stats["arrivals_added"] += 1
                else:
                    sid = int(await conn.fetchval(
                        """
                        INSERT INTO shops
                            (name, address, description, chain_name,
                             cycle_length, anchor_date, is_active)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                        RETURNING id
                        """,
                        p["name"], p["address"], p["description"], p["chain_name"],
                        p["cycle_length"], p["anchor_date"], p["is_active"],
                    ))
                    stats["shops_new"] += 1
                    if p["anchor_date"]:
                        await conn.execute(
                            "INSERT INTO arrivals (shop_id, arrival_date, source, set_by) "
                            "VALUES ($1, $2, 'sheet', $3)",
                            sid, p["anchor_date"], actor_tg_id,
                        )
                        stats["arrivals_added"] += 1
            except Exception as e:
                log.exception("sheets row failed: %s", p.get("name"))
                stats["errors"].append(f"{p.get('name')}: {e}")
                stats["skipped"] += 1
    return stats
