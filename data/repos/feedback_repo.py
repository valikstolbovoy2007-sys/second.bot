from data.db import pool


async def save_feedback(user_id: int, text: str, shop_id: int | None = None) -> int:
    async with pool().acquire() as conn:
        return int(await conn.fetchval(
            "INSERT INTO feedback (user_id, text, shop_id) VALUES ($1, $2, $3) RETURNING id",
            user_id, text, shop_id,
        ))


async def get_feedback(fb_id: int) -> dict | None:
    async with pool().acquire() as conn:
        r = await conn.fetchrow(
            """
            SELECT f.id, f.user_id, f.shop_id, f.text, f.status, f.assigned_to, f.created_at,
                   u.tg_id, u.username,
                   s.name AS shop_name
            FROM feedback f
            LEFT JOIN users u ON u.id = f.user_id
            LEFT JOIN shops s ON s.id = f.shop_id
            WHERE f.id = $1
            """,
            fb_id,
        )
    return dict(r) if r else None


async def list_feedback(
    *,
    limit: int = 20,
    offset: int = 0,
    status: str | None = None,
    shop_ids: list[int] | None = None,
) -> list[dict]:
    where = []
    args: list = []

    if status:
        args.append(status)
        where.append(f"f.status = ${len(args)}")
    if shop_ids is not None:
        args.append(shop_ids)
        where.append(f"f.shop_id = ANY(${len(args)}::int[])")

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    args.extend([limit, offset])

    async with pool().acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT f.id, f.user_id, f.shop_id, f.text, f.status, f.assigned_to, f.created_at,
                   u.tg_id, u.username,
                   s.name AS shop_name
            FROM feedback f
            LEFT JOIN users u ON u.id = f.user_id
            LEFT JOIN shops s ON s.id = f.shop_id
            {where_sql}
            ORDER BY f.created_at DESC
            LIMIT ${len(args) - 1} OFFSET ${len(args)}
            """,
            *args,
        )
    return [dict(r) for r in rows]


async def set_feedback_status(fb_id: int, status: str) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            "UPDATE feedback SET status = $1 WHERE id = $2",
            status, fb_id,
        )


async def set_feedback_assignee(fb_id: int, admin_tg_id: int | None) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            "UPDATE feedback SET assigned_to = $1 WHERE id = $2",
            admin_tg_id, fb_id,
        )
