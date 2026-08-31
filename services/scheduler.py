import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from data.repos.notifier_repo import get_last_run, set_last_run
from services import parsers as parser_svc
from services.notifier import run_for_minute

log = logging.getLogger(__name__)

TZ = ZoneInfo("Europe/Moscow")  # Sevastopol time
CATCHUP_LIMIT_HOURS = 2

_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler | None:
    return _scheduler


def _now() -> datetime:
    return datetime.now(TZ).replace(second=0, microsecond=0)


async def _tick(bot: Bot) -> None:
    now = _now()
    try:
        await run_for_minute(bot, now)
    except Exception:
        log.exception("notifier tick failed")
    await set_last_run(now)


async def _catch_up(bot: Bot, now: datetime) -> None:
    last = await get_last_run()
    if last is None:
        return
    if last.tzinfo is None:
        last = last.replace(tzinfo=TZ)
    gap = now - last
    if gap <= timedelta(minutes=1):
        return
    if gap > timedelta(hours=CATCHUP_LIMIT_HOURS):
        log.info("skipping catch-up: gap %s exceeds limit", gap)
        return

    today_start = now.replace(hour=0, minute=0)
    cursor = max(last + timedelta(minutes=1), today_start)
    log.info("catching up from %s to %s", cursor, now)
    while cursor <= now:
        try:
            await run_for_minute(bot, cursor)
        except Exception:
            log.exception("catch-up tick failed at %s", cursor)
        cursor += timedelta(minutes=1)


async def _parser_job(key: str) -> None:
    try:
        await parser_svc.run_parser(key, triggered_by="cron", apply=True)
    except Exception:
        log.exception("scheduled parser %s failed", key)


async def _register_parser_jobs(scheduler: AsyncIOScheduler) -> None:
    for key in parser_svc.list_keys():
        try:
            cron = await parser_svc.get_cron(key)
            trigger = CronTrigger.from_crontab(cron, timezone=TZ)
        except Exception:
            log.exception("bad cron for parser %s, skipping", key)
            continue
        scheduler.add_job(
            _parser_job,
            trigger,
            args=[key],
            id=f"parser_{key}",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
        log.info("scheduled parser %s with cron=%s", key, cron)


async def start_scheduler(bot: Bot) -> AsyncIOScheduler:
    global _scheduler
    now = _now()
    await _catch_up(bot, now)
    await set_last_run(now)

    scheduler = AsyncIOScheduler(timezone=TZ)
    scheduler.add_job(
        _tick,
        CronTrigger(second=0, timezone=TZ),
        args=[bot],
        id="notifier_tick",
        max_instances=1,
        coalesce=True,
    )
    await _register_parser_jobs(scheduler)
    scheduler.start()
    _scheduler = scheduler
    log.info("scheduler started, ticks every minute")
    return scheduler


async def reschedule_parser(scheduler: AsyncIOScheduler, key: str) -> bool:
    """Re-create the cron job for a parser using the latest config value."""
    try:
        cron = await parser_svc.get_cron(key)
        trigger = CronTrigger.from_crontab(cron, timezone=TZ)
    except Exception:
        log.exception("bad cron for parser %s", key)
        return False
    scheduler.add_job(
        _parser_job,
        trigger,
        args=[key],
        id=f"parser_{key}",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    return True
