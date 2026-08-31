from aiogram import Router

from handlers.superadmin import (
    admins,
    audit_view,
    chains,
    config,
    db_ops,
    errors_view,
    health,
    maintenance,
    panel,
    template,
    texts,
)

router = Router(name="superadmin")
router.include_router(panel.router)
router.include_router(admins.router)
router.include_router(audit_view.router)
router.include_router(chains.router)
router.include_router(config.router)
router.include_router(db_ops.router)
router.include_router(errors_view.router)
router.include_router(health.router)
router.include_router(maintenance.router)
router.include_router(template.router)
router.include_router(texts.router)
