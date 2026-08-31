import logging

import asyncpg

from config import settings

log = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          SERIAL PRIMARY KEY,
    tg_id       BIGINT UNIQUE NOT NULL,
    username    TEXT,
    notify_time TIME NOT NULL DEFAULT '09:00',
    pause_until DATE,
    is_blocked  BOOLEAN NOT NULL DEFAULT false,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS shops (
    id            SERIAL PRIMARY KEY,
    name          TEXT NOT NULL,
    address       TEXT NOT NULL,
    description   TEXT,
    chain_name    TEXT,
    cycle_length  INT,
    anchor_date   DATE,
    price_start   INT,
    price_step    INT,
    -- Свободный текст: «Пн-Сб 10:00–21:00, Вс выходной» и т.п.
    -- Не парсим в дни/часы — слишком разнообразные форматы у секондов.
    working_hours TEXT,
    is_active     BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS subscriptions (
    user_id             INT  NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    shop_id             INT  NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    notify_arrival      BOOLEAN NOT NULL DEFAULT true,
    notify_max_discount BOOLEAN NOT NULL DEFAULT true,
    notify_middle       BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (user_id, shop_id)
);

CREATE TABLE IF NOT EXISTS notification_weekdays (
    user_id INT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    shop_id INT      NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    weekday SMALLINT NOT NULL CHECK (weekday BETWEEN 0 AND 6),
    PRIMARY KEY (user_id, shop_id, weekday)
);

CREATE TABLE IF NOT EXISTS sent_notifications (
    user_id    INT  NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    shop_id    INT  NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    sent_date  DATE NOT NULL,
    PRIMARY KEY (user_id, shop_id, event_type, sent_date)
);

CREATE TABLE IF NOT EXISTS admins (
    tg_id      BIGINT PRIMARY KEY,
    role       TEXT NOT NULL DEFAULT 'admin' CHECK (role IN ('admin','super_admin')),
    added_by   BIGINT,
    added_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    note       TEXT
);

CREATE TABLE IF NOT EXISTS shop_assignments (
    shop_id      INT    NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    admin_tg_id  BIGINT NOT NULL REFERENCES admins(tg_id) ON DELETE CASCADE,
    assigned_by  BIGINT,
    assigned_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (shop_id, admin_tg_id)
);

CREATE TABLE IF NOT EXISTS chains (
    id            SERIAL PRIMARY KEY,
    name          TEXT UNIQUE NOT NULL,
    default_cycle INT,
    parser_key    TEXT,
    sort_order    INT NOT NULL DEFAULT 0,
    is_active     BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS arrivals (
    id           SERIAL PRIMARY KEY,
    shop_id      INT NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    arrival_date DATE NOT NULL,
    source       TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual','parser','sheet')),
    set_by       BIGINT,
    set_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS arrivals_shop_idx ON arrivals(shop_id, arrival_date DESC);

CREATE TABLE IF NOT EXISTS shop_photos (
    id          SERIAL PRIMARY KEY,
    shop_id     INT NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    file_id     TEXT NOT NULL,
    ord         INT NOT NULL DEFAULT 0,
    uploaded_by BIGINT,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS user_blocks (
    tg_id      BIGINT PRIMARY KEY,
    reason     TEXT,
    blocked_by BIGINT,
    blocked_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS feedback (
    id          SERIAL PRIMARY KEY,
    user_id     INT REFERENCES users(id) ON DELETE SET NULL,
    shop_id     INT REFERENCES shops(id) ON DELETE SET NULL,
    text        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new','in_progress','closed')),
    assigned_to BIGINT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS admin_messages (
    id          SERIAL PRIMARY KEY,
    from_tg_id  BIGINT NOT NULL,
    to_tg_id    BIGINT NOT NULL,
    text        TEXT NOT NULL,
    feedback_id INT REFERENCES feedback(id) ON DELETE SET NULL,
    sent_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS broadcasts (
    id              SERIAL PRIMARY KEY,
    payload         JSONB NOT NULL,
    audience_filter JSONB NOT NULL,
    scheduled_at    TIMESTAMPTZ,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','running','paused','done','cancelled','failed')),
    sent            INT NOT NULL DEFAULT 0,
    failed          INT NOT NULL DEFAULT 0,
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    created_by      BIGINT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_log (
    id          SERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor_tg_id BIGINT,
    action      TEXT NOT NULL,
    target_type TEXT,
    target_id   TEXT,
    payload     JSONB
);
CREATE INDEX IF NOT EXISTS audit_ts_idx ON audit_log(ts DESC);
CREATE INDEX IF NOT EXISTS audit_actor_idx ON audit_log(actor_tg_id);

CREATE TABLE IF NOT EXISTS error_log (
    id             SERIAL PRIMARY KEY,
    ts             TIMESTAMPTZ NOT NULL DEFAULT now(),
    level          TEXT NOT NULL DEFAULT 'ERROR',
    source         TEXT,
    message        TEXT,
    traceback      TEXT,
    update_payload JSONB,
    signature      TEXT,
    resolved       BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS error_sig_idx ON error_log(signature);

CREATE TABLE IF NOT EXISTS parser_runs (
    id           SERIAL PRIMARY KEY,
    parser_key   TEXT NOT NULL,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    status       TEXT NOT NULL DEFAULT 'running'
                 CHECK (status IN ('running','ok','error','dry_run')),
    triggered_by TEXT NOT NULL DEFAULT 'manual'
                 CHECK (triggered_by IN ('manual','cron','dry_run')),
    actor_tg_id  BIGINT,
    result       JSONB,
    error_text   TEXT
);
CREATE INDEX IF NOT EXISTS parser_runs_key_idx ON parser_runs(parser_key, started_at DESC);

CREATE TABLE IF NOT EXISTS bot_config (
    key         TEXT PRIMARY KEY,
    value       TEXT,
    type        TEXT NOT NULL DEFAULT 'str' CHECK (type IN ('str','int','bool','time','url','json')),
    description TEXT,
    updated_by  BIGINT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS scheduler_state (
    id           INT PRIMARY KEY DEFAULT 1,
    last_run_at  TIMESTAMPTZ,
    CONSTRAINT scheduler_state_singleton CHECK (id = 1)
);

CREATE TABLE IF NOT EXISTS bot_texts (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_by  BIGINT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO scheduler_state (id) VALUES (1) ON CONFLICT DO NOTHING;
"""

# Inline migrations for existing installs (idempotent ALTER TABLE).
MIGRATIONS = """
ALTER TABLE admins   ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'admin';
ALTER TABLE admins   ADD COLUMN IF NOT EXISTS added_by BIGINT;
ALTER TABLE admins   ADD COLUMN IF NOT EXISTS added_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE admins   ADD COLUMN IF NOT EXISTS note TEXT;
ALTER TABLE feedback        ADD COLUMN IF NOT EXISTS shop_id INT REFERENCES shops(id) ON DELETE SET NULL;
ALTER TABLE feedback        ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'new';
ALTER TABLE feedback        ADD COLUMN IF NOT EXISTS assigned_to BIGINT;
ALTER TABLE admin_messages  ADD COLUMN IF NOT EXISTS feedback_id INT REFERENCES feedback(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS admin_messages_fb_idx ON admin_messages(feedback_id);
ALTER TABLE shops           ADD COLUMN IF NOT EXISTS price_start INT;
ALTER TABLE shops           ADD COLUMN IF NOT EXISTS price_step  INT;
ALTER TABLE shops           ADD COLUMN IF NOT EXISTS working_hours TEXT;
ALTER TABLE shops           ADD COLUMN IF NOT EXISTS maps_url TEXT;
-- photo_file_id отжил: ровно одна колонка-фото в shops дублировала
-- многострочную таблицу shop_photos. Переносим оставшиеся значения и
-- удаляем колонку. Обе операции идемпотентны — повторный запуск ничего
-- не сломает (после DROP COLUMN селект из этой колонки в WHERE не
-- выполняется, потому что INSERT идёт через подзапрос).
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'shops' AND column_name = 'photo_file_id'
    ) THEN
        INSERT INTO shop_photos (shop_id, file_id, ord)
        SELECT id, photo_file_id, 0 FROM shops
        WHERE photo_file_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM shop_photos sp WHERE sp.shop_id = shops.id);
        ALTER TABLE shops DROP COLUMN photo_file_id;
    END IF;
END$$;
CREATE INDEX IF NOT EXISTS subscriptions_user_idx        ON subscriptions(user_id);
CREATE INDEX IF NOT EXISTS subscriptions_shop_idx        ON subscriptions(shop_id);
CREATE INDEX IF NOT EXISTS notification_weekdays_user_idx ON notification_weekdays(user_id);
CREATE INDEX IF NOT EXISTS shop_assignments_admin_idx    ON shop_assignments(admin_tg_id);
CREATE INDEX IF NOT EXISTS sent_notifications_user_idx   ON sent_notifications(user_id);
"""


async def init_db() -> None:
    global _pool
    _pool = await asyncpg.create_pool(
        settings.DATABASE_URL,
        min_size=3,
        max_size=20,
        command_timeout=30,
    )
    async with _pool.acquire() as conn:
        await conn.execute(SCHEMA)
        await conn.execute(MIGRATIONS)
        for admin_id in settings.ADMIN_IDS:
            await conn.execute(
                "INSERT INTO admins (tg_id, role) VALUES ($1, 'admin') ON CONFLICT DO NOTHING",
                admin_id,
            )
        for super_id in settings.SUPER_ADMIN_IDS:
            await conn.execute(
                """
                INSERT INTO admins (tg_id, role) VALUES ($1, 'super_admin')
                ON CONFLICT (tg_id) DO UPDATE SET role = 'super_admin'
                """,
                super_id,
            )
    log.info("DB initialised")


async def close_db() -> None:
    if _pool is not None:
        await _pool.close()
        log.info("DB pool closed")


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialised — call init_db() first")
    return _pool
