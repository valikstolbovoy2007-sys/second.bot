import html
import logging
import traceback

from aiogram import Bot, Router
from aiogram.types import CallbackQuery, ErrorEvent

from config import settings
from services.error_log import write_error

log = logging.getLogger(__name__)
router = Router(name="errors")
unhandled_router = Router(name="unhandled")


@unhandled_router.callback_query()
async def on_unhandled_cb(call: CallbackQuery) -> None:
    from services.texts import t
    log.warning("unhandled callback_data=%r from user=%s", call.data, call.from_user.id if call.from_user else None)
    await call.answer(await t("system.unhandled_button"), show_alert=False)


@router.errors()
async def on_error(event: ErrorEvent, bot: Bot) -> None:
    log.exception("unhandled exception", exc_info=event.exception)

    update_dump = event.update.model_dump(exclude_none=True)
    update_repr = str(update_dump)[:1500]
    tb = "".join(traceback.format_exception(event.exception))
    message = f"{type(event.exception).__name__}: {event.exception}"

    source = "unknown"
    try:
        if event.update.message:
            source = "message"
        elif event.update.callback_query:
            source = "callback_query"
        elif event.update.my_chat_member:
            source = "my_chat_member"
    except Exception:
        pass

    await write_error(
        source=source,
        message=message,
        traceback_text=tb,
        update_payload=update_dump,
    )

    if not settings.ADMIN_CHAT_ID:
        return

    tb_short = tb[-1500:]
    text = (
        "🐞 <b>Unhandled exception</b>\n\n"
        f"<b>Update:</b>\n<code>{html.escape(update_repr)}</code>\n\n"
        f"<b>Traceback:</b>\n<pre>{html.escape(tb_short)}</pre>"
    )
    try:
        await bot.send_message(settings.ADMIN_CHAT_ID, text)
    except Exception:
        log.exception("failed to forward error to admin chat")
