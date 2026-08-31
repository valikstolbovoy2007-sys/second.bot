from data.db import pool


async def all_user_tg_ids() -> list[int]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT tg_id FROM users WHERE is_blocked = false"
        )
    return [int(r["tg_id"]) for r in rows]


async def stats() -> dict:
    async with pool().acquire() as conn:
        users_total = await conn.fetchval("SELECT count(*) FROM users")
        users_active = await conn.fetchval("SELECT count(*) FROM users WHERE is_blocked = false")
        subs_total = await conn.fetchval("SELECT count(*) FROM subscriptions")
        shops_total = await conn.fetchval(
            "SELECT count(*) FROM shops WHERE is_active = true"
        )
        sent_today = await conn.fetchval(
            "SELECT count(*) FROM sent_notifications WHERE sent_date = CURRENT_DATE"
        )
        top = await conn.fetch(
            """
            SELECT s.name, s.chain_name, count(*) AS n
            FROM subscriptions sub
            JOIN shops s ON s.id = sub.shop_id
            GROUP BY s.id
            ORDER BY n DESC
            LIMIT 10
            """,
        )
    return {
        "users_total": int(users_total or 0),
        "users_active": int(users_active or 0),
        "subs_total": int(subs_total or 0),
        "shops_total": int(shops_total or 0),
        "sent_today": int(sent_today or 0),
        "top_shops": [(r["chain_name"], r["name"], int(r["n"])) for r in top],
    }
