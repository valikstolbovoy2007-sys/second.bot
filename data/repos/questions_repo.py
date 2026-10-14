from data.db import pool


async def save_question(user_id: int, shop_id: int, text: str, photo_id: str | None = None) -> int:
    async with pool().acquire() as conn:
        return int(await conn.fetchval(
            "INSERT INTO questions (user_id, shop_id, text, photo_id) VALUES ($1, $2, $3, $4) RETURNING id",
            user_id, shop_id, text, photo_id,
        ))


async def get_question(q_id: int) -> dict | None:
    async with pool().acquire() as conn:
        r = await conn.fetchrow(
            """
            SELECT q.id, q.user_id, q.shop_id, q.text, q.photo_id, q.status, q.created_at,
                   u.tg_id, u.username, s.name AS shop_name
            FROM questions q
            LEFT JOIN users u ON u.id = q.user_id
            LEFT JOIN shops s ON s.id = q.shop_id
            WHERE q.id = $1
            """,
            q_id,
        )
    return dict(r) if r else None


async def set_question_status(q_id: int, status: str) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            "UPDATE questions SET status = $1 WHERE id = $2", status, q_id,
        )


async def add_question_message(question_id: int, from_tg_id: int, to_tg_id: int, text: str) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            "INSERT INTO question_messages (question_id, from_tg_id, to_tg_id, text) VALUES ($1,$2,$3,$4)",
            question_id, from_tg_id, to_tg_id, text,
        )


async def list_question_messages(question_id: int, limit: int = 20) -> list[dict]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT from_tg_id, text, sent_at
            FROM question_messages
            WHERE question_id = $1
            ORDER BY sent_at ASC
            LIMIT $2
            """,
            question_id, limit,
        )
    return [dict(r) for r in rows]
