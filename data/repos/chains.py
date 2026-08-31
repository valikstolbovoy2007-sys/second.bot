"""Repo for the `chains` table.

Chains are network groups that shops can belong to.
"""
from dataclasses import dataclass

from data.db import pool


@dataclass(slots=True)
class Chain:
    id: int
    name: str
    default_cycle: int | None
    parser_key: str | None
    sort_order: int
    is_active: bool


def _row(r) -> Chain:
    return Chain(
        id=r["id"],
        name=r["name"],
        default_cycle=r["default_cycle"],
        parser_key=r["parser_key"],
        sort_order=r["sort_order"],
        is_active=r["is_active"],
    )


async def list_chains(only_active: bool = False) -> list[Chain]:
    where = "WHERE is_active = true" if only_active else ""
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            f"SELECT id, name, default_cycle, parser_key, sort_order, is_active "
            f"FROM chains {where} ORDER BY sort_order, name"
        )
    return [_row(r) for r in rows]


async def get_chain(chain_id: int) -> Chain | None:
    async with pool().acquire() as conn:
        r = await conn.fetchrow(
            "SELECT id, name, default_cycle, parser_key, sort_order, is_active "
            "FROM chains WHERE id = $1",
            chain_id,
        )
    return _row(r) if r else None


async def create_chain(
    *,
    name: str,
    default_cycle: int | None = None,
    parser_key: str | None = None,
    sort_order: int = 0,
) -> int:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO chains (name, default_cycle, parser_key, sort_order, is_active)
            VALUES ($1, $2, $3, $4, true)
            RETURNING id
            """,
            name,
            default_cycle,
            parser_key,
            sort_order,
        )
    return int(row["id"])


async def update_chain_field(chain_id: int, field: str, value) -> None:
    if field not in {"name", "default_cycle", "parser_key", "sort_order"}:
        raise ValueError(f"forbidden field: {field}")
    async with pool().acquire() as conn:
        await conn.execute(
            f"UPDATE chains SET {field} = $1 WHERE id = $2",
            value,
            chain_id,
        )


async def set_active(chain_id: int, active: bool) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            "UPDATE chains SET is_active = $1 WHERE id = $2",
            active,
            chain_id,
        )


async def delete_chain(chain_id: int) -> None:
    async with pool().acquire() as conn:
        await conn.execute("DELETE FROM chains WHERE id = $1", chain_id)


async def shops_in_chain(chain_name: str) -> int:
    async with pool().acquire() as conn:
        r = await conn.fetchrow(
            "SELECT COUNT(*) AS n FROM shops WHERE chain_name = $1",
            chain_name,
        )
    return int(r["n"])
