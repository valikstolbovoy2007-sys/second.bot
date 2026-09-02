import asyncio
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode

from config import settings
from data.db import close_db, init_db
from handlers import (
    admin,
    catalog,
    errors,
    feedback,
    my_shops,
    settings as settings_handlers,
    start,
)
from handlers import superadmin
from middlewares.maintenance import MaintenanceMiddleware
from middlewares.throttling import ThrottlingMiddleware
from services.broadcasts import start_dispatcher, stop_dispatcher
from services.scheduler import start_scheduler
from services.texts import warm_cache as warm_texts_cache


def _setup_logging() -> None:
    Path("logs").mkdir(exist_ok=True)
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    handlers: list[logging.Handler] = [
        logging.StreamHandler(),
        RotatingFileHandler("logs/bot.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"),
    ]
    logging.basicConfig(level=settings.LOG_LEVEL, format=fmt, handlers=handlers)


async def main() -> None:
    _setup_logging()
    log = logging.getLogger("bot")

    await init_db()
    await warm_texts_cache()

    session = AiohttpSession(proxy=settings.PROXY_URL) if settings.PROXY_URL else None
    if settings.PROXY_URL:
        log.info("using proxy %s", settings.PROXY_URL)
    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        session=session,
    )
    dp = Dispatcher()
    dp.message.middleware(MaintenanceMiddleware())
    dp.callback_query.middleware(MaintenanceMiddleware())
    dp.callback_query.middleware(ThrottlingMiddleware(rate=0.5))
    dp.include_router(errors.router)
    dp.include_router(superadmin.router)
    dp.include_router(admin.router)
    dp.include_router(feedback.router)
    dp.include_router(start.router)
    dp.include_router(catalog.router)
    dp.include_router(my_shops.router)
    dp.include_router(settings_handlers.router)
    dp.include_router(errors.unhandled_router)

    scheduler = await start_scheduler(bot)
    await start_dispatcher(bot)

    log.info("starting polling")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await stop_dispatcher()
        scheduler.shutdown(wait=False)
        await close_db()
        await bot.session.close()
        log.info("stopped")


if __name__ == "__main__":
    asyncio.run(main())
