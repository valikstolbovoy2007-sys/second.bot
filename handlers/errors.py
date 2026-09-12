import html
import logging
import traceback

from aiogram import Bot, Router
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter
from aiogram.types import CallbackQuery, ErrorEvent

from config import settings
from services.error_log import write_error

log = logging.getLogger(__name__)
router = Router(name="errors")
unhandled_router = Router(name="unhandled")

# Substrings of TelegramBadRequest messages that are transient noise rather
# than real bugs — e.g. the callback query itself expired (Telegram gives
# ~a few dozen seconds to answer it) because a request took unusually long.
_NOISY_BAD_REQUEST_SNIPPETS = (
    "query is too old",
)


def _is_noise(exc: Exception) -> bool:
    if isinstance(exc, (TelegramNetworkError, TelegramRetryAfter)):
        return True
    if isinstance(exc, TelegramBadRequest):
        text = str(exc).lower()
        return any(s in text for s in _NOISY_BAD_REQUEST_SNIPPETS)
    return False


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
    # control, an expired callback query) aren't actionable — the request
    # usually still went through. Log them (above) for diagnostics, but
    # don't spam the admin chat.
    if _is_noise(event.exception):
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
        msg = await bot.send_message(settings.ADMIN_CHAT_ID, text)
        from services.chat_journal import journal
        journal.record(settings.ADMIN_CHAT_ID, msg.message_id)
    except Exception:
        log.exception("failed to forward error to admin chat")
