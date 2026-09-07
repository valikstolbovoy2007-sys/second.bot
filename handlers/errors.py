import html
import logging
import traceback

from aiogram import Bot, Router
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
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
    tb = "".join(traceback.format_exception(event.exception))
    message = f"{type(event.exception).__name__}: {event.exception}"

    source = "unknown"
    from_user = None
    try:
        if event.update.message:
            source = "message"
            from_user = event.update.message.from_user
        elif event.update.callback_query:
            source = "callback_query"
            from_user = event.update.callback_query.from_user
        elif event.update.my_chat_member:
            source = "my_chat_member"
            from_user = event.update.my_chat_member.from_user
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

    # Transient Telegram-side hiccups (slow/dropped HTTP response, flood
    # control) aren't actionable — the request usually still went through.
    # Log them (above) for diagnostics, but don't spam the admin chat.
    if isinstance(event.exception, (TelegramNetworkError, TelegramRetryAfter)):
        return

    if from_user:
        who = f"@{from_user.username}" if from_user.username else str(from_user.id)
    else:
        who = "—"

    # Last few lines of the traceback — where the actual error is described —
    # rather than the full stack, which is mostly library internals.
    tb_lines = [ln for ln in tb.splitlines() if ln.strip()]
    tb_tail = "\n".join(tb_lines[-8:])

    text = (
        "🐞 <b>Ошибка</b>\n\n"
        f"👤 {html.escape(who)}\n"
        f"❗ <code>{html.escape(message)}</code>\n\n"
        f"<pre>{html.escape(tb_tail)}</pre>"
    )
    try:
        await bot.send_message(settings.ADMIN_CHAT_ID, text)
    except Exception:
        log.exception("failed to forward error to admin chat")
