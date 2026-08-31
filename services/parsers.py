"""Parsers registry and run-tracking.

Each parser is a small async function (or coroutine factory) keyed by
`parser_key`. We record every run in `parser_runs` so admins can see history,
duration, and any error message.

Schedule + enabled flags live in `bot_config` so super-admins can configure
them via the existing config UI:
    parser.<key>.enabled       'true'|'false'
    parser.<key>.cron          cron expression, "0 9 * * *" by default
"""
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from data.db import pool
from data.repos.admin_roles import visible_shop_ids
from data.repos.shops import list_shops_scoped, set_arrival, set_chain_arrival
from services.audit import write as audit_write
from services.config_live import get as cfg_get
from services.megahand_parser import fetch_megahand_arrival
from services.sheets_parser import import_sheets

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ParserDef:
    key: str
    label: str
    chain_name: str
    kind: str = "anchor"  # "anchor" returns one date; "import" upserts many shops
    fetch: Callable[[], Awaitable[date | None]] | None = None
    import_fn: Callable[..., Awaitable[dict[str, Any]]] | None = None


REGISTRY: dict[str, ParserDef] = {
    "megahand": ParserDef(
        key="megahand",
        label="Megahand (sevastopol.mhand.ru)",
        chain_name="Megahand",
        kind="anchor",
        fetch=fetch_megahand_arrival,
    ),
    "sheets": ParserDef(
        key="sheets",
        label="Google Sheets (импорт магазинов)",
        chain_name="*",
        kind="import",
        import_fn=import_sheets,
    ),
}


async def is_enabled(key: str) -> bool:
    val = await cfg_get(f"parser.{key}.enabled", "true")
    return str(val).lower() in {"true", "1", "yes"}


async def get_cron(key: str) -> str:
    return str(await cfg_get(f"parser.{key}.cron", "0 9 * * *"))


async def _start_run(key: str, triggered_by: str, actor_tg_id: int | None) -> int:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO parser_runs (parser_key, status, triggered_by, actor_tg_id)
            VALUES ($1, 'running', $2, $3)
            RETURNING id
            """,
            key, triggered_by, actor_tg_id,
        )
    return int(row["id"])


async def _finish_run(
    run_id: int,
    *,
    status: str,
    result: dict | None = None,
    error_text: str | None = None,
) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            """
            UPDATE parser_runs
            SET status = $2, finished_at = now(), result = $3::jsonb, error_text = $4
            WHERE id = $1
            """,
            run_id,
            status,
            json.dumps(result, default=str, ensure_ascii=False) if result else None,
            error_text,
        )


async def run_parser(
    key: str,
    *,
    triggered_by: str = "manual",
    actor_tg_id: int | None = None,
    apply: bool = True,
    actor_scope: list[int] | None = None,
) -> dict[str, Any]:
    """Run a single parser end-to-end.

    `apply=False` performs a dry-run: fetch the date but don't update shops.
    `actor_scope` limits the update to specific shop_ids (used for non-super
    admins who only see their own shops).
    """
    pdef = REGISTRY.get(key)
    if not pdef:
        raise ValueError(f"unknown parser: {key}")
    if not await is_enabled(key) and triggered_by == "cron":
        log.info("parser %s skipped: disabled", key)
        return {"skipped": "disabled"}

    run_id = await _start_run(key, "dry_run" if not apply else triggered_by, actor_tg_id)
    try:
        if pdef.kind == "import":
            assert pdef.import_fn is not None
            stats = await pdef.import_fn(apply=apply, actor_tg_id=actor_tg_id)
            status = "dry_run" if not apply else "ok"
            await _finish_run(run_id, status=status, result=stats)
            await audit_write(
                actor_tg_id, f"parser.{key}.run", "parser", key,
                {**stats, "triggered_by": triggered_by, "apply": apply},
            )
            return {**stats, "anchor": None, "updated": stats.get("shops_updated", 0)}

        assert pdef.fetch is not None
        anchor = await pdef.fetch()
        if anchor is None:
            await _finish_run(run_id, status="ok", result={"anchor": None})
            return {"anchor": None, "updated": 0}

        if not apply:
            await _finish_run(run_id, status="dry_run", result={"anchor": anchor.isoformat()})
            return {"anchor": anchor.isoformat(), "updated": 0, "dry_run": True}

        if actor_scope is None:
            n = await set_chain_arrival(pdef.chain_name, anchor)
        else:
            items, _ = await list_shops_scoped(actor_scope, 500, 0)
            targets = [s.id for s in items if s.chain_name == pdef.chain_name]
            for sid in targets:
                await set_arrival(sid, anchor)
            n = len(targets)

        await _finish_run(run_id, status="ok",
                          result={"anchor": anchor.isoformat(), "updated": n})
        await audit_write(
            actor_tg_id, f"parser.{key}.run", "chain", pdef.chain_name,
            {"anchor": anchor.isoformat(), "updated": n,
             "triggered_by": triggered_by, "scoped": actor_scope is not None},
        )
        return {"anchor": anchor.isoformat(), "updated": n}
    except Exception as e:
        log.exception("parser %s failed", key)
        await _finish_run(run_id, status="error", error_text=str(e)[:1000])
        return {"error": str(e)}


async def list_runs(key: str | None = None, limit: int = 20, offset: int = 0) -> list[dict]:
    where = "WHERE parser_key = $3" if key else ""
    args: list = [limit, offset]
    if key:
        args.append(key)
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, parser_key, started_at, finished_at, status,
                   triggered_by, actor_tg_id, result, error_text
            FROM parser_runs
            {where}
            ORDER BY started_at DESC
            LIMIT $1 OFFSET $2
            """,
            *args,
        )
    out = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("result"), str):
            try:
                d["result"] = json.loads(d["result"])
            except (TypeError, ValueError):
                pass
        out.append(d)
    return out


def list_keys() -> list[str]:
    return list(REGISTRY.keys())


async def resolve_actor_scope(actor_tg_id: int) -> list[int] | None:
    return await visible_shop_ids(actor_tg_id)
