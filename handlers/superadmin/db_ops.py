import html
import logging
import os
from datetime import date

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from data.db import pool
from handlers.admin.filters import IsSuperAdmin
from handlers.admin.ui import safe_edit
from services.audit import write as audit_write
from services.json_io import deserialize, export_dump, import_dump, serialize

log = logging.getLogger(__name__)
router = Router(name="sa_db")
router.message.filter(IsSuperAdmin())
router.callback_query.filter(IsSuperAdmin())


class DbStates(StatesGroup):
    json_upload = State()
    json_replace_choice = State()
    sql_query = State()
    restore_upload = State()
    restore_confirm = State()


@router.callback_query(F.data == "sa:db:menu")
async def cb_menu(call: CallbackQuery) -> None:
    rows = [
        [InlineKeyboardButton(text="📊 Размер БД", callback_data="sa:db:size")],
        [InlineKeyboardButton(text="🧰 Бэкап (pg_dump)", callback_data="sa:db:backup")],
        [InlineKeyboardButton(text="♻️ Восстановить из .sql.gz", callback_data="sa:db:restore")],
        [InlineKeyboardButton(text="⬇️ Экспорт магазинов (JSON)", callback_data="sa:db:expjson")],
        [InlineKeyboardButton(text="⬆️ Импорт магазинов (JSON)", callback_data="sa:db:impjson")],
        [InlineKeyboardButton(text="🧹 Очистка старых уведомлений (>90д)", callback_data="sa:db:cleanup")],
        [InlineKeyboardButton(text="🧪 SQL-консоль (SELECT)", callback_data="sa:db:sql")],
        [InlineKeyboardButton(text="❤️ Health-check", callback_data="sa:db:health")],
        [InlineKeyboardButton(text="← Назад", callback_data="sa:menu")],
    ]
    await safe_edit(call, "🧰 <b>База данных</b>", InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


@router.callback_query(F.data == "sa:db:size")
async def cb_size(call: CallbackQuery) -> None:
    async with pool().acquire() as conn:
        size = await conn.fetchval("SELECT pg_size_pretty(pg_database_size(current_database()))")
        rows = await conn.fetch(
            """
            SELECT relname, pg_size_pretty(pg_total_relation_size(relid)) AS sz, n_live_tup AS rows
            FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 15
            """
        )
    lines = [f"📊 <b>БД: {size}</b>", "", "<b>Таблицы:</b>"]
    for r in rows:
        lines.append(f"  • {r['relname']}: {r['sz']} ({r['rows']} строк)")
    await safe_edit(call, "\n".join(lines),
                    InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="← Назад", callback_data="sa:db:menu")]]))
    await call.answer()


@router.callback_query(F.data == "sa:db:backup")
async def cb_backup(call: CallbackQuery) -> None:
    import asyncio
    import gzip
    import shutil
    from datetime import datetime
    from pathlib import Path

    from config import settings

    Path("backups").mkdir(exist_ok=True)
    name = f"backups/secondbot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sql.gz"
    pg_dump = shutil.which("pg_dump")
    if not pg_dump:
        await call.answer("pg_dump не найден в PATH", show_alert=True)
        return
    await call.message.answer("⏳ Делаю бэкап…")
    proc = await asyncio.create_subprocess_exec(
        pg_dump, settings.DATABASE_URL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        await call.message.answer(f"❌ pg_dump ошибка:\n<code>{err.decode(errors='ignore')[:500]}</code>")
        await call.answer()
        return
    with gzip.open(name, "wb") as f:
        f.write(out)
    size_kb = os.path.getsize(name) // 1024
    await audit_write(call.from_user.id, "db.backup", "db", name, {"size_kb": size_kb})
    try:
        await call.message.answer_document(FSInputFile(name), caption=f"✅ Бэкап {size_kb} KiB")
    except Exception as e:
        await call.message.answer(f"Бэкап создан: {name} ({size_kb} KiB), но не отправился: {e}")
    await call.answer()


# ---------- JSON export ----------

@router.callback_query(F.data == "sa:db:expjson")
async def cb_export_json(call: CallbackQuery) -> None:
    await call.message.answer("⏳ Готовлю выгрузку…")
    dump = await export_dump()
    raw = serialize(dump)
    fname = f"shops_{date.today().isoformat()}.json"
    await audit_write(
        call.from_user.id, "db.export_json", "db", fname,
        {"shops": len(dump.get("shops", [])), "chains": len(dump.get("chains", []))},
    )
    await call.message.answer_document(
        BufferedInputFile(raw, filename=fname),
        caption=f"⬇️ Магазинов: {len(dump.get('shops', []))}, сетей: {len(dump.get('chains', []))}",
    )
    await call.answer()


# ---------- JSON import ----------

@router.callback_query(F.data == "sa:db:impjson")
async def cb_import_prompt(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(DbStates.json_upload)
    await call.message.answer(
        "⬆️ Пришли .json файл, экспортированный из этого бота.\n"
        "После загрузки спрошу, делать ли upsert по (имя, адрес).\n"
        "/cancel — выйти."
    )
    await call.answer()


@router.message(DbStates.json_upload, F.text == "/cancel")
async def msg_imp_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено.")


@router.message(DbStates.json_upload, F.document)
async def msg_imp_doc(message: Message, state: FSMContext, bot: Bot) -> None:
    doc = message.document
    if not (doc.file_name or "").lower().endswith(".json"):
        await message.answer("Нужен .json. Пришли ещё раз.")
        return
    if doc.file_size and doc.file_size > 10 * 1024 * 1024:
        await message.answer("Файл слишком большой (>10 MB).")
        return
    file = await bot.get_file(doc.file_id)
    buf = await bot.download_file(file.file_path)
    raw = buf.read()
    try:
        dump = deserialize(raw)
    except Exception as e:
        await message.answer(f"❌ Не валидный JSON: {e}")
        return
    n_shops = len(dump.get("shops") or [])
    n_chains = len(dump.get("chains") or [])
    await state.update_data(dump=dump)
    await state.set_state(DbStates.json_replace_choice)
    await message.answer(
        f"📦 В файле: магазинов {n_shops}, сетей {n_chains}.\n\n"
        "Как импортировать?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить новые (без замены)",
                                  callback_data="sa:db:imp:add")],
            [InlineKeyboardButton(text="🔁 Upsert по (имя, адрес)",
                                  callback_data="sa:db:imp:upsert")],
            [InlineKeyboardButton(text="✖️ Отмена", callback_data="sa:db:imp:cancel")],
        ]),
    )


@router.callback_query(DbStates.json_replace_choice, F.data == "sa:db:imp:cancel")
async def cb_imp_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.answer("Импорт отменён.")
    await call.answer()


@router.callback_query(DbStates.json_replace_choice, F.data.in_({"sa:db:imp:add", "sa:db:imp:upsert"}))
async def cb_imp_apply(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    dump = data.get("dump")
    replace = call.data == "sa:db:imp:upsert"
    await state.clear()
    if not dump:
        await call.answer("Нет данных", show_alert=True)
        return
    await call.message.answer("⏳ Импортирую…")
    try:
        result = await import_dump(dump, replace_shops=replace)
    except Exception as e:
        log.exception("import_dump failed")
        await call.message.answer(f"❌ Ошибка: {e}")
        await call.answer()
        return
    await audit_write(
        call.from_user.id, "db.import_json", "db", None,
        {"replace": replace, **result},
    )
    await call.message.answer(
        "✅ Импортировано:\n"
        f"  Сетей: {result['chains']}\n"
        f"  Новых магазинов: {result['shops_new']}\n"
        f"  Обновлено магазинов: {result['shops_updated']}\n"
        f"  Фотографий: {result['photos']}"
    )
    await call.answer("Готово")


@router.callback_query(F.data == "sa:db:cleanup")
async def cb_cleanup(call: CallbackQuery) -> None:
    async with pool().acquire() as conn:
        result = await conn.execute(
            "DELETE FROM sent_notifications WHERE sent_date < current_date - INTERVAL '90 days'"
        )
    n = int(result.split()[-1]) if result.startswith("DELETE") else 0
    await audit_write(call.from_user.id, "db.cleanup_notifications", "db", None, {"deleted": n})
    await call.answer(f"Удалено: {n}", show_alert=True)


# ---------- DB restore from .sql.gz ----------

def _db_name_from_url(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).path.lstrip("/") or "?"


@router.callback_query(F.data == "sa:db:restore")
async def cb_restore_prompt(call: CallbackQuery, state: FSMContext) -> None:
    from config import settings
    db = _db_name_from_url(settings.DATABASE_URL)
    await state.set_state(DbStates.restore_upload)
    await call.message.answer(
        "♻️ <b>Восстановление БД</b>\n\n"
        "Это <b>полностью перезапишет</b> текущую базу данных содержимым "
        "загружаемого дампа. Откатить операцию нельзя.\n\n"
        f"Текущая БД: <code>{html.escape(db)}</code>\n\n"
        "Пришли .sql.gz файл (созданный pg_dump). Или /cancel — выйти.",
    )
    await call.answer()


@router.message(DbStates.restore_upload, F.text == "/cancel")
async def msg_restore_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено.")


@router.message(DbStates.restore_upload, F.document)
async def msg_restore_doc(message: Message, state: FSMContext, bot: Bot) -> None:
    doc = message.document
    name = (doc.file_name or "").lower()
    if not name.endswith(".sql.gz"):
        await message.answer("Нужен .sql.gz. Пришли ещё раз или /cancel.")
        return
    if doc.file_size and doc.file_size > 200 * 1024 * 1024:
        await message.answer("Файл слишком большой (>200 MB).")
        return
    from pathlib import Path
    Path("backups").mkdir(exist_ok=True)
    local = f"backups/restore_{message.from_user.id}_{int(date.today().toordinal())}.sql.gz"
    file = await bot.get_file(doc.file_id)
    await bot.download_file(file.file_path, destination=local)
    from config import settings
    db = _db_name_from_url(settings.DATABASE_URL)
    await state.update_data(restore_path=local, restore_db=db)
    await state.set_state(DbStates.restore_confirm)
    size_kb = os.path.getsize(local) // 1024
    await message.answer(
        f"📦 Файл получен: {size_kb} KiB.\n\n"
        f"⚠️ <b>ВНИМАНИЕ:</b> после подтверждения текущая БД будет полностью перезаписана.\n"
        f"Чтобы продолжить, пришли точное имя текущей базы: <code>{html.escape(db)}</code>\n\n"
        f"Любой другой текст или /cancel — отмена.",
    )


@router.message(DbStates.restore_confirm, F.text)
async def msg_restore_confirm(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    expected = data.get("restore_db")
    path = data.get("restore_path")
    text = message.text.strip()
    if text == "/cancel" or text != expected:
        await state.clear()
        try:
            if path:
                os.remove(path)
        except OSError:
            pass
        await message.answer("Отменено.")
        return
    await state.clear()
    import asyncio
    import shutil

    from config import settings

    psql = shutil.which("psql")
    gunzip = shutil.which("gunzip") or shutil.which("gzip")
    if not psql:
        await message.answer("❌ psql не найден в PATH.")
        return
    if not gunzip:
        await message.answer("❌ gunzip/gzip не найден в PATH.")
        return
    await message.answer("⏳ Восстанавливаю… Это может занять время.")
    decomp = await asyncio.create_subprocess_exec(
        gunzip, "-c", path,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    psql_proc = await asyncio.create_subprocess_exec(
        psql, "-q", "-v", "ON_ERROR_STOP=1", settings.DATABASE_URL,
        stdin=decomp.stdout,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    if decomp.stdout:
        decomp.stdout.close()
    psql_out, psql_err = await psql_proc.communicate()
    await decomp.wait()
    rc = psql_proc.returncode
    try:
        os.remove(path)
    except OSError:
        pass
    await audit_write(
        message.from_user.id, "db.restore", "db", expected,
        {"rc": rc, "stderr_tail": (psql_err or b"")[-300:].decode(errors="ignore")},
    )
    if rc != 0:
        tail = (psql_err or b"")[-800:].decode(errors="ignore")
        await message.answer(
            f"❌ Восстановление не удалось (rc={rc}):\n<pre>{html.escape(tail)}</pre>"
        )
        return
    await message.answer(
        "✅ База восстановлена. Перезапусти бота, чтобы соединения подобрали актуальную схему."
    )


_FORBIDDEN_SQL = (
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "GRANT",
    "REVOKE", "CREATE", "COMMENT", "VACUUM", "REINDEX", "CLUSTER",
    "COPY", "CALL", "DO", "REFRESH", "LOCK", "SET", "RESET",
)


def _is_safe_select(sql: str) -> tuple[bool, str]:
    s = sql.strip().rstrip(";").strip()
    if not s:
        return False, "Пустой запрос."
    if ";" in s:
        return False, "Точка с запятой запрещена (один запрос за раз)."
    upper = s.upper()
    if not (upper.startswith("SELECT") or upper.startswith("WITH") or upper.startswith("EXPLAIN")):
        return False, "Разрешены только SELECT / WITH / EXPLAIN."
    for kw in _FORBIDDEN_SQL:
        if f" {kw} " in f" {upper} " or f" {kw}(" in f" {upper}":
            return False, f"Запрещённое ключевое слово: {kw}"
    return True, ""


@router.callback_query(F.data == "sa:db:sql")
async def cb_sql_prompt(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(DbStates.sql_query)
    await call.message.answer(
        "🧪 <b>SQL-консоль</b> (только SELECT/WITH/EXPLAIN)\n\n"
        "Пришли один SQL-запрос. Лимит 200 строк (LIMIT добавлю сам, если нет).\n"
        "/cancel — выйти.",
    )
    await call.answer()


@router.message(DbStates.sql_query, F.text == "/cancel")
async def msg_sql_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено.")


@router.message(DbStates.sql_query, F.text)
async def msg_sql_run(message: Message, state: FSMContext) -> None:
    raw = message.text.strip()
    ok, err = _is_safe_select(raw)
    if not ok:
        await message.answer(f"❌ {err}")
        return
    sql = raw.rstrip(";").strip()
    if "LIMIT" not in sql.upper() and not sql.upper().startswith("EXPLAIN"):
        sql = f"{sql} LIMIT 200"
    import time
    t0 = time.time()
    try:
        async with pool().acquire() as conn:
            async with conn.transaction(readonly=True):
                rows = await conn.fetch(sql)
    except Exception as e:
        await message.answer(f"❌ Ошибка: <code>{str(e)[:500]}</code>")
        return
    ms = int((time.time() - t0) * 1000)
    await audit_write(
        message.from_user.id, "db.sql_select", "db", None,
        {"rows": len(rows), "ms": ms, "sql": raw[:300]},
    )
    if not rows:
        await state.clear()
        await message.answer(f"✅ 0 строк, {ms} мс")
        return
    cols = list(rows[0].keys())
    if len(rows) <= 30 and sum(len(str(r)) for r in rows) < 3500:
        lines = [f"✅ {len(rows)} строк, {ms} мс", "", " | ".join(cols)]
        for r in rows:
            lines.append(" | ".join(str(r[c])[:50] for c in cols))
        text = "<pre>" + "\n".join(lines) + "</pre>"
        if len(text) < 4000:
            await state.clear()
            await message.answer(text)
            return
    import io
    buf = io.StringIO()
    buf.write("\ufeff" + ";".join(cols) + "\n")
    for r in rows:
        buf.write(";".join(str(r[c]).replace(";", ",").replace("\n", " ") for c in cols) + "\n")
    data = buf.getvalue().encode("utf-8")
    await state.clear()
    await message.answer_document(
        BufferedInputFile(data, filename=f"query_{date.today().isoformat()}.csv"),
        caption=f"✅ {len(rows)} строк, {ms} мс",
    )


@router.callback_query(F.data == "sa:db:health")
async def cb_health(call: CallbackQuery) -> None:
    import time
    t0 = time.time()
    try:
        async with pool().acquire() as conn:
            await conn.fetchval("SELECT 1")
        db_ms = int((time.time() - t0) * 1000)
        text = f"❤️ DB: OK ({db_ms} ms)"
    except Exception as e:
        text = f"❤️ DB: ❌ {e}"
    await safe_edit(call, text, InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← Назад", callback_data="sa:db:menu")]
    ]))
    await call.answer()
