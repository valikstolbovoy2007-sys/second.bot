"""JSON export/import of shops + chains + photos (TZ §10.9).

Schema:
{
  "version": 1,
  "exported_at": "...",
  "chains": [{name, default_cycle, parser_key, sort_order, is_active}, ...],
  "shops": [{id?, name, address, description, chain_name, cycle_length,
             anchor_date, is_active,
             photos: [{file_id, ord}, ...]}, ...]
}
"""
import json
import logging
from datetime import date, datetime
from typing import Any

from data.db import pool

log = logging.getLogger(__name__)

VERSION = 1


def _to_jsonable(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


async def export_dump() -> dict[str, Any]:
    async with pool().acquire() as conn:
        chain_rows = await conn.fetch(
            """
            SELECT name, default_cycle, parser_key, sort_order, is_active
            FROM chains ORDER BY sort_order, name
            """
        )
        shop_rows = await conn.fetch(
            """
            SELECT id, name, address, description, chain_name, cycle_length,
                   anchor_date, price_start, price_step, is_active
            FROM shops ORDER BY id
            """
        )
        photo_rows = await conn.fetch(
            "SELECT shop_id, file_id, ord FROM shop_photos ORDER BY shop_id, ord"
        )

    photos_by_shop: dict[int, list[dict]] = {}
    for p in photo_rows:
        photos_by_shop.setdefault(int(p["shop_id"]), []).append({
            "file_id": p["file_id"],
            "ord": int(p["ord"]),
        })

    shops_out: list[dict] = []
    for s in shop_rows:
        shops_out.append({
            "id": int(s["id"]),
            "name": s["name"],
            "address": s["address"],
            "description": s["description"],
            "chain_name": s["chain_name"],
            "cycle_length": s["cycle_length"],
            "anchor_date": _to_jsonable(s["anchor_date"]),
            "price_start": s["price_start"],
            "price_step": s["price_step"],
            "is_active": bool(s["is_active"]),
            "photos": photos_by_shop.get(int(s["id"]), []),
        })

    return {
        "version": VERSION,
        "exported_at": datetime.utcnow().isoformat(),
        "chains": [
            {
                "name": c["name"],
                "default_cycle": c["default_cycle"],
                "parser_key": c["parser_key"],
                "sort_order": int(c["sort_order"]),
                "is_active": bool(c["is_active"]),
            } for c in chain_rows
        ],
        "shops": shops_out,
    }


async def import_dump(data: dict[str, Any], *, replace_shops: bool = False) -> dict[str, int]:
    """Import a dump produced by `export_dump`.

    Behaviour:
      - chains: upsert by name
      - shops:  if `replace_shops` is True, existing shops are matched by
                (name, address) and updated; otherwise new shops are inserted
                (a duplicate is allowed). Photos are appended (not replaced).
    """
    if data.get("version") != VERSION:
        raise ValueError(f"unsupported version: {data.get('version')}")
    chains = data.get("chains") or []
    shops = data.get("shops") or []

    n_chains = 0
    n_shops_new = 0
    n_shops_upd = 0
    n_photos = 0

    async with pool().acquire() as conn:
        async with conn.transaction():
            for c in chains:
                await conn.execute(
                    """
                    INSERT INTO chains (name, default_cycle, parser_key, sort_order, is_active)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (name) DO UPDATE
                      SET default_cycle = EXCLUDED.default_cycle,
                          parser_key = EXCLUDED.parser_key,
                          sort_order = EXCLUDED.sort_order,
                          is_active = EXCLUDED.is_active
                    """,
                    c["name"], c.get("default_cycle"), c.get("parser_key"),
                    int(c.get("sort_order") or 0), bool(c.get("is_active", True)),
                )
                n_chains += 1

            for s in shops:
                anchor = s.get("anchor_date")
                if isinstance(anchor, str):
                    try:
                        anchor = date.fromisoformat(anchor[:10])
                    except ValueError:
                        anchor = None

                shop_id = None
                if replace_shops:
                    existing = await conn.fetchrow(
                        "SELECT id FROM shops WHERE name = $1 AND address = $2",
                        s["name"], s["address"],
                    )
                    if existing:
                        shop_id = int(existing["id"])
                        await conn.execute(
                            """
                            UPDATE shops
                            SET description = $2, chain_name = $3, cycle_length = $4,
                                anchor_date = $5, price_start = $6, price_step = $7,
                                is_active = $8
                            WHERE id = $1
                            """,
                            shop_id,
                            s.get("description"), s.get("chain_name"),
                            s.get("cycle_length"), anchor,
                            s.get("price_start"), s.get("price_step"),
                            bool(s.get("is_active", True)),
                        )
                        n_shops_upd += 1

                if shop_id is None:
                    row = await conn.fetchrow(
                        """
                        INSERT INTO shops (name, address, description, chain_name,
                                           cycle_length, anchor_date,
                                           price_start, price_step, is_active)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                        RETURNING id
                        """,
                        s["name"], s["address"], s.get("description"),
                        s.get("chain_name"), s.get("cycle_length"), anchor,
                        s.get("price_start"), s.get("price_step"),
                        bool(s.get("is_active", True)),
                    )
                    shop_id = int(row["id"])
                    n_shops_new += 1

                for ph in (s.get("photos") or []):
                    if not ph.get("file_id"):
                        continue
                    next_ord = await conn.fetchval(
                        "SELECT COALESCE(MAX(ord), -1) + 1 FROM shop_photos WHERE shop_id = $1",
                        shop_id,
                    )
                    await conn.execute(
                        """
                        INSERT INTO shop_photos (shop_id, file_id, ord)
                        VALUES ($1, $2, $3)
                        """,
                        shop_id, ph["file_id"], int(next_ord or 0),
                    )
                    n_photos += 1

    return {
        "chains": n_chains,
        "shops_new": n_shops_new,
        "shops_updated": n_shops_upd,
        "photos": n_photos,
    }


def serialize(dump: dict[str, Any]) -> bytes:
    return json.dumps(dump, ensure_ascii=False, indent=2, default=_to_jsonable).encode("utf-8")


def deserialize(raw: bytes) -> dict[str, Any]:
    return json.loads(raw.decode("utf-8"))
