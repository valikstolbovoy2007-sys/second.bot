from dataclasses import dataclass
from datetime import date

from data.db import pool


@dataclass(frozen=True)
class Shop:
    id: int
    name: str
    address: str
    description: str | None
    chain_name: str | None
    cycle_length: int | None
    anchor_date: date | None
    price_start: int | None
    price_step: int | None
    # Свободный текст: «Пн-Сб 10:00–21:00, Вс выходной». Может быть None.
    working_hours: str | None
    is_active: bool
    maps_url: str | None


def _row_to_shop(row) -> Shop:
    return Shop(
        id=row["id"],
        name=row["name"],
        address=row["address"],
        description=row["description"],
        chain_name=row["chain_name"],
        cycle_length=row["cycle_length"],
        anchor_date=row["anchor_date"],
        price_start=row["price_start"],
        price_step=row["price_step"],
        working_hours=row["working_hours"],
        is_active=row["is_active"],
        maps_url=row["maps_url"],
    )


def _filter_clause(flt: str) -> str:
    if flt == "chain":
        return "AND chain_name IS NOT NULL"
    if flt == "indep":
        return "AND chain_name IS NULL"
    return ""


async def list_shops(flt: str, limit: int, offset: int) -> tuple[list[Shop], int]:
    where_extra = _filter_clause(flt)
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT *, count(*) OVER () AS _total FROM shops
            WHERE is_active = true {where_extra}
            ORDER BY chain_name NULLS LAST, name
            LIMIT $1 OFFSET $2
            """,
            limit,
            offset,
        )
    total = int(rows[0]["_total"]) if rows else 0
    return [_row_to_shop(r) for r in rows], total


async def get_shop(shop_id: int) -> Shop | None:
    async with pool().acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM shops WHERE id = $1", shop_id)
    return _row_to_shop(row) if row else None


async def count_shops() -> int:
    async with pool().acquire() as conn:
        return int(await conn.fetchval("SELECT count(*) FROM shops") or 0)


async def create_shop(
    name: str,
    address: str,
    description: str | None = None,
    chain_name: str | None = None,
    cycle_length: int | None = None,
    anchor_date: date | None = None,
    price_start: int | None = None,
    price_step: int | None = None,
    working_hours: str | None = None,
) -> int:
    async with pool().acquire() as conn:
        return int(await conn.fetchval(
            """
            INSERT INTO shops (
                name, address, description, chain_name,
                cycle_length, anchor_date, price_start, price_step,
                working_hours
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            RETURNING id
            """,
            name, address, description, chain_name,
            cycle_length, anchor_date, price_start, price_step,
            working_hours,
        ))


async def find_similar_shops(name: str, address: str, limit: int = 3) -> list[Shop]:
    """Detect potential duplicates by fuzzy name OR address match."""
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM shops
            WHERE name ILIKE $1 OR address ILIKE $2
            ORDER BY chain_name NULLS LAST, name
            LIMIT $3
            """,
            f"%{name}%", f"%{address}%", limit,
        )
    return [_row_to_shop(r) for r in rows]


async def delete_shop(shop_id: int) -> bool:
    async with pool().acquire() as conn:
        result = await conn.execute("DELETE FROM shops WHERE id = $1", shop_id)
    return result == "DELETE 1"


async def set_arrival(shop_id: int, anchor: date) -> bool:
    async with pool().acquire() as conn:
        result = await conn.execute(
            "UPDATE shops SET anchor_date = $2 WHERE id = $1",
            shop_id, anchor,
        )
    return result == "UPDATE 1"


async def set_chain_arrival(chain_name: str, anchor: date) -> int:
    async with pool().acquire() as conn:
        result = await conn.execute(
            "UPDATE shops SET anchor_date = $2 WHERE chain_name = $1",
            chain_name, anchor,
        )
    return int(result.split()[-1]) if result.startswith("UPDATE") else 0


async def list_all_shops() -> list[Shop]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM shops ORDER BY chain_name NULLS LAST, name"
        )
    return [_row_to_shop(r) for r in rows]


async def list_active_shops() -> list[Shop]:
    """All active shops, no DB-level filter or sort.

    Used by the new catalog filtering pipeline (see services/catalog.py)
    where filtering and sorting happen in Python.
    """
    async with pool().acquire() as conn:
        rows = await conn.fetch("SELECT * FROM shops WHERE is_active = true")
    return [_row_to_shop(r) for r in rows]


async def list_shops_scoped(
    scope_ids: list[int] | None,
    limit: int,
    offset: int,
    only_inactive: bool = False,
    search: str | None = None,
) -> tuple[list[Shop], int]:
    """scope_ids=None — без ограничений (super_admin)."""
    if scope_ids is not None and not scope_ids:
        return [], 0
    where = ["1=1"]
    args: list = []
    i = 1
    if scope_ids is not None:
        where.append(f"id = ANY(${i})")
        args.append(scope_ids)
        i += 1
    if only_inactive:
        where.append("is_active = false")
    if search:
        where.append(f"(name ILIKE ${i} OR address ILIKE ${i})")
        args.append(f"%{search}%")
        i += 1
    where_sql = " AND ".join(where)
    args_list = args + [limit, offset]
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT *, count(*) OVER () AS _total FROM shops
            WHERE {where_sql}
            ORDER BY chain_name NULLS LAST, name
            LIMIT ${i} OFFSET ${i + 1}
            """,
            *args_list,
        )
    total = int(rows[0]["_total"]) if rows else 0
    return [_row_to_shop(r) for r in rows], total


async def update_shop_field(shop_id: int, field: str, value) -> bool:
    allowed = {"name", "address", "description", "chain_name",
               "cycle_length", "anchor_date", "is_active",
               "price_start", "price_step", "working_hours","maps_url"}
    if field not in allowed:
        raise ValueError(f"field {field} not editable")
    async with pool().acquire() as conn:
        result = await conn.execute(
            f"UPDATE shops SET {field} = $2 WHERE id = $1",
            shop_id, value,
        )
    return result == "UPDATE 1"


async def deactivate_shop(shop_id: int) -> bool:
    return await update_shop_field(shop_id, "is_active", False)


async def activate_shop(shop_id: int) -> bool:
    return await update_shop_field(shop_id, "is_active", True)


async def shop_subscribers_count(shop_id: int) -> int:
    async with pool().acquire() as conn:
        return int(await conn.fetchval(
            "SELECT count(*) FROM subscriptions WHERE shop_id = $1", shop_id
        ) or 0)
