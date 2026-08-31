"""Super-admin UI for editing user-facing bot texts.

Texts are stored in `bot_texts(key, value, ...)`. Defaults live in
`services/texts.py`. Admin edits a text by sending a regular Telegram
message with native formatting (bold/italic/links via long-press) —
the bot reads `message.html_text` and saves the resulting HTML.
"""
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from data.repos.texts import (
    delete_override,
    get_meta,
    list_overridden_keys,
    set_override,
)
from handlers.admin.filters import IsSuperAdmin
from handlers.admin.ui import safe_edit
from services.audit import write as audit_write
from services.texts import (
    DEFAULTS,
    DESCRIPTIONS,
    GROUPS,
    PLACEHOLDERS,
    invalidate,
    render_preview,
    validate,
)

log = logging.getLogger(__name__)
router = Router(name="sa_texts")
router.message.filter(IsSuperAdmin())
router.callback_query.filter(IsSuperAdmin())


class TxtCb(CallbackData, prefix="satxt"):
    action: str            # groups, group, key, edit, reset_ask, reset_yes, cancel_edit, save, edit_again
    value: str | None = None


class TxtStates(StatesGroup):
    waiting_text = State()
    waiting_save = State()


# ---------- helpers ----------

def _truncate(s: str, n: int = 60) -> str:
    one_line = s.replace("\n", " ⏎ ")
    return one_line if len(one_line) <= n else one_line[: n - 1] + "…"


def _group_by_key(text_key: str) -> str:
    return text_key.split(".", 1)[0]


async def _groups_view(call: CallbackQuery) -> None:
    overridden = await list_overridden_keys()
    rows: list[list[InlineKeyboardButton]] = []
    for gid, title, keys in GROUPS:
        n = sum(1 for k in keys if k in overridden)
        suffix = f" — {len(keys)} текст." + (f", ✏️ {n}" if n else "")
        rows.append([InlineKeyboardButton(
            text=f"{title}{suffix}",
            callback_data=TxtCb(action="group", value=gid).pack(),
        )])
    rows.append([InlineKeyboardButton(text="← В супер-админку", callback_data="sa:menu")])
    text = (
        "📝 <b>Тексты бота</b>\n\n"
        "Здесь можно отредактировать текст любого сообщения и кнопки, "
        "которые видит пользователь.\n\n"
        "Выбери раздел:"
    )
    await safe_edit(call, text, InlineKeyboardMarkup(inline_keyboard=rows))


async def _group_view(call: CallbackQuery, gid: str) -> None:
    grp = next((g for g in GROUPS if g[0] == gid), None)
    if not grp:
        await call.answer("Раздел не найден", show_alert=True)
        return
    _, title, keys = grp
    overridden = await list_overridden_keys()
    rows: list[list[InlineKeyboardButton]] = []
    for k in keys:
        marker = "✏️ " if k in overridden else ""
        desc = DESCRIPTIONS.get(k, k)
        rows.append([InlineKeyboardButton(
            text=f"{marker}{_truncate(desc, 50)}",
            callback_data=TxtCb(action="key", value=k).pack(),
        )])
    rows.append([InlineKeyboardButton(
        text="← К разделам",
        callback_data=TxtCb(action="groups").pack(),
    )])
    text = f"📝 <b>{title}</b>\n\nВыбери текст для редактирования:"
    await safe_edit(call, text, InlineKeyboardMarkup(inline_keyboard=rows))


async def _key_card(call: CallbackQuery, key: str) -> None:
    if key not in DEFAULTS:
        await call.answer("Неизвестный ключ", show_alert=True)
        return
    overridden = await list_overridden_keys()
    is_overridden = key in overridden
    # Read raw value (override > default)
    from services.texts import _get_raw
    current = await _get_raw(key)

    placeholders = sorted(PLACEHOLDERS.get(key, set()))
    ph_line = (
        "🔧 Подстановки: " + ", ".join("{" + p + "}" for p in placeholders)
        if placeholders else "🔧 Подстановок нет"
    )
    src_line = "✏️ Переопределён супер-админом" if is_overridden else "📦 Стандартный (не редактировался)"
    desc = DESCRIPTIONS.get(key, "—")

    info = (
        f"🔑 <code>{key}</code>\n"
        f"📝 {desc}\n"
        f"{ph_line}\n"
        f"{src_line}"
    )
    rows = [
        [InlineKeyboardButton(
            text="✏️ Изменить",
            callback_data=TxtCb(action="edit", value=key).pack(),
        )],
    ]
    if is_overridden:
        rows.append([InlineKeyboardButton(
            text="🔄 Сбросить к стандартному",
            callback_data=TxtCb(action="reset_ask", value=key).pack(),
        )])
    gid = _group_by_key(key)
    rows.append([InlineKeyboardButton(
        text="← К списку",
        callback_data=TxtCb(action="group", value=gid).pack(),
    )])

    # Send the info as a chat message edit, then send the rendered preview as a fresh msg.
    await safe_edit(call, info, InlineKeyboardMarkup(inline_keyboard=rows))
    preview = render_preview(key, current)
    try:
        await call.message.answer(
            "👁 <b>Как видит пользователь:</b>\n─────────────────────\n" + preview
        )
    except TelegramBadRequest as exc:
        await call.message.answer(f"⚠️ Не удалось отрендерить превью: {exc!s}")


# ---------- entrypoints ----------

@router.callback_query(TxtCb.filter(F.action == "groups"))
async def cb_groups(call: CallbackQuery, callback_data: TxtCb, state: FSMContext) -> None:
    await state.clear()
    await _groups_view(call)
    await call.answer()


@router.callback_query(F.data == "sa:texts")
async def cb_open(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _groups_view(call)
    await call.answer()


@router.callback_query(TxtCb.filter(F.action == "group"))
async def cb_group(call: CallbackQuery, callback_data: TxtCb) -> None:
    await _group_view(call, callback_data.value or "")
    await call.answer()


@router.callback_query(TxtCb.filter(F.action == "key"))
async def cb_key(call: CallbackQuery, callback_data: TxtCb) -> None:
    await _key_card(call, callback_data.value or "")
    await call.answer()


# ---------- edit flow ----------

EDIT_INTRO = (
    "✏️ <b>Редактирование текста</b>\n"
    "<code>{key}</code>\n\n"
    "📝 {desc}\n"
    "{ph_line}\n\n"
    "Пришли новый текст следующим сообщением.\n\n"
    "💡 <b>Как форматировать:</b>\n"
    "Выдели слово в чате и используй долгое нажатие — "
    "там есть <b>Жирный</b>, <i>Курсив</i>, "
    "<u>Подчёркивание</u>, <s>Зачёркнутый</s>, <code>Моноширинный</code>, ссылки и цитаты. "
    "Бот сам сохранит твоё форматирование.\n\n"
    "🔧 Подстановки типа <code>{{username}}</code> пиши обычным текстом в фигурных скобках — "
    "бот подставит реальное значение при отправке.\n\n"
    "Чтобы отменить — жми кнопку ниже."
)


@router.callback_query(TxtCb.filter(F.action == "edit"))
async def cb_edit_start(call: CallbackQuery, callback_data: TxtCb, state: FSMContext) -> None:
    key = callback_data.value or ""
    if key not in DEFAULTS:
        await call.answer("Неизвестный ключ", show_alert=True)
        return
    placeholders = sorted(PLACEHOLDERS.get(key, set()))
    ph_line = (
        "🔧 В этом тексте можно использовать: " +
        ", ".join("<code>{" + p + "}</code>" for p in placeholders)
        if placeholders else "🔧 Подстановок в этом тексте быть не должно."
    )
    intro = EDIT_INTRO.format(
        key=key,
        desc=DESCRIPTIONS.get(key, "—"),
        ph_line=ph_line,
    )
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="✖️ Отмена",
            callback_data=TxtCb(action="key", value=key).pack(),
        )
    ]])
    await state.set_state(TxtStates.waiting_text)
    await state.update_data(key=key)
    await safe_edit(call, intro, cancel_kb)
    await call.answer()


@router.message(TxtStates.waiting_text, F.text)
async def msg_receive_text(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    key = data.get("key")
    if not key:
        await state.clear()
        await message.answer("Состояние потерялось, открой /sudo заново.")
        return

    new_value = message.html_text
    ok, err = validate(key, new_value)
    if not ok:
        await message.answer(
            f"❌ <b>Не получилось сохранить:</b>\n{err}\n\n"
            f"Попробуй ещё раз — пришли новый текст следующим сообщением."
        )
        return  # FSM stays — admin can resubmit

    preview = render_preview(key, new_value)
    await state.update_data(new_value=new_value)
    await state.set_state(TxtStates.waiting_save)

    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="✅ Сохранить",
            callback_data=TxtCb(action="save", value=key).pack(),
        )],
        [InlineKeyboardButton(
            text="✏️ Переписать",
            callback_data=TxtCb(action="edit_again", value=key).pack(),
        )],
        [InlineKeyboardButton(
            text="✖️ Отмена",
            callback_data=TxtCb(action="key", value=key).pack(),
        )],
    ])
    await message.answer(
        "👁 <b>Предпросмотр (как увидит пользователь):</b>\n"
        "─────────────────────\n"
        f"{preview}\n"
        "─────────────────────\n\n"
        "Сохранить?",
        reply_markup=confirm_kb,
    )


@router.callback_query(TxtCb.filter(F.action == "edit_again"))
async def cb_edit_again(call: CallbackQuery, callback_data: TxtCb, state: FSMContext) -> None:
    key = callback_data.value or ""
    await state.set_state(TxtStates.waiting_text)
    await state.update_data(key=key, new_value=None)
    await call.message.answer("Жду новый текст следующим сообщением.")
    await call.answer()


@router.callback_query(TxtCb.filter(F.action == "save"))
async def cb_save(call: CallbackQuery, callback_data: TxtCb, state: FSMContext) -> None:
    data = await state.get_data()
    key = data.get("key") or callback_data.value
    new_value = data.get("new_value")
    if not key or new_value is None:
        await call.answer("Сессия редактирования потеряна.", show_alert=True)
        await state.clear()
        return
    ok, err = validate(key, new_value)
    if not ok:
        await call.answer(f"Не прошло валидацию: {err}", show_alert=True)
        return

    from services.texts import _get_raw
    before = await _get_raw(key)
    await set_override(key, new_value, by=call.from_user.id)
    invalidate(key)
    await audit_write(
        call.from_user.id, "text.update", "text", key,
        {"before": before[:500], "after": new_value[:500]},
    )
    await state.clear()
    await call.message.answer("✅ Текст сохранён.")
    await _key_card(call, key)
    await call.answer()


# ---------- reset flow ----------

@router.callback_query(TxtCb.filter(F.action == "reset_ask"))
async def cb_reset_ask(call: CallbackQuery, callback_data: TxtCb) -> None:
    key = callback_data.value or ""
    if key not in DEFAULTS:
        await call.answer("Неизвестный ключ", show_alert=True)
        return
    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🔄 Да, сбросить",
            callback_data=TxtCb(action="reset_yes", value=key).pack(),
        )],
        [InlineKeyboardButton(
            text="✖️ Отмена",
            callback_data=TxtCb(action="key", value=key).pack(),
        )],
    ])
    await safe_edit(
        call,
        f"🔄 Сбросить текст <code>{key}</code> к стандартному?\n\n"
        "Твоё переопределение будет удалено.",
        confirm_kb,
    )
    await call.answer()


@router.callback_query(TxtCb.filter(F.action == "reset_yes"))
async def cb_reset_yes(call: CallbackQuery, callback_data: TxtCb) -> None:
    key = callback_data.value or ""
    if key not in DEFAULTS:
        await call.answer("Неизвестный ключ", show_alert=True)
        return
    ok = await delete_override(key)
    invalidate(key)
    if ok:
        await audit_write(call.from_user.id, "text.reset", "text", key, None)
        await call.answer("Сброшено к стандартному")
    else:
        await call.answer("Уже стандартный")
    await _key_card(call, key)
