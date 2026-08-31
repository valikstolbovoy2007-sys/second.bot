from aiogram import Router

from handlers.admin import (
    add_shop,
    arrivals,
    broadcasts,
    feedback,
    legacy,
    panel,
    parsers,
    shops,
    shops_extra,
    stats,
    users,
)

router = Router(name="admin")
router.include_router(panel.router)
router.include_router(shops.router)
router.include_router(shops_extra.router)
router.include_router(add_shop.router)
router.include_router(arrivals.router)
router.include_router(broadcasts.router)
router.include_router(feedback.router)
router.include_router(parsers.router)
router.include_router(stats.router)
router.include_router(users.router)
router.include_router(legacy.router)
