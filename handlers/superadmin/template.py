"""Super-admin UI for editing the shop card template.

Two modes coexist:
  - block constructor (default, simpler): toggle/move/edit per-block
  - raw HTML mode (legacy, full power): one big DSL template string

The block constructor stores its state in `bot_config[shop_card_blocks]`
as JSON. The raw mode stores into `bot_config[shop_card_template]` as a
string. At render time the raw template takes priority if present (see
`services/card_render.py`).
"""
import html
import logging
from datetime import date, timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from data.repos.shops import Shop
from handlers.admin.filters import IsSuperAdmin
from handlers.admin.ui import safe_edit
from services.audit import write as audit_write
from services.card_blocks import (
    CONFIG_KEY_BLOCKS,
    BlockConfig,
    assemble as assemble_blocks,
    from_json as blocks_from_json,
    get_spec,
    move as move_block,
    reset_block,
    set_custom_text,
    simplify_for_user,
    to_json as blocks_to_json,
    toggle as toggle_block,
    validate_custom_text,
)
from services.card_render import render_with_template
from services.card_template import (
    CONFIG_KEY,
    DEFAULT_TEMPLATE,
    VARIABLES,
    render,
    validate,
)
from services.config_live import get as cfg_get
from services.config_live import set_value as cfg_set

log = logging.getLogger(__name__)
router = Router(name="sa_template")
router.message.filter(IsSuperAdmin())
router.callback_query.filter(IsSuperAdmin())


class TplStates(StatesGroup):
    raw_waiting = State()        # raw-mode: waiting for full template
    block_waiting = State()      # block-mode: waiting for one block's text


# Description for hint in the variables list — picks names from VARIABLES.
_VAR_DESCR = {name: descr for name, descr in VARIABLES}


def _sample_shop() -> Shop:
    return Shop(
        id=0,
        name="Megahand Центральный",
        address="ул. Ленина, 12",
        description="Большой ассортимент, есть детская секция.",
        chain_name="Megahand",
        cycle_length=14,
        anchor_date=date.today() - timedelta(days=3),
        price_start=1200,
        price_step=80,
        working_hours="Пн-Сб 10:00–21:00, Вс выходной",
        is_active=True,
    )


async def _load_blocks() -> list[BlockConfig]:
    raw = await cfg_get(CONFIG_KEY_BLOCKS, None)
    return blocks_from_json(raw)


async def _save_blocks(blocks: list[BlockConfig], by: int) -> None:
    await cfg_set(
        CONFIG_KEY_BLOCKS,
        blocks_to_json(blocks),
        type_="str",
        description="Блоки шаблона карточки магазина",
        by=by,
    )


async def _raw_template() -> str | None:
    raw = await cfg_get(CONFIG_KEY, None)
    return raw if raw else None


# =====================================================================
# BLOCK CONSTRUCTOR (default UI)
# =====================================================================


def _blocks_kb(blocks: list[BlockConfig], raw_active: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    last_idx = len(blocks) - 1
    for i, cfg in enumerate(blocks):
        spec = get_spec(cfg.id)
        if spec is None:
            continue
        title_btn = InlineKeyboardButton(
            text=spec.title,
            callback_data=f"sa:tpl:bview:{cfg.id}",
        )
        toggle_btn = InlineKeyboardButton(
            text="✅" if cfg.enabled else "⬜",
            callback_data=f"sa:tpl:btoggle:{cfg.id}",
        )
        up_cb = f"sa:tpl:bup:{cfg.id}" if i > 0 else "sa:tpl:noop"
        down_cb = f"sa:tpl:bdown:{cfg.id}" if i < last_idx else "sa:tpl:noop"
        up_btn = InlineKeyboardButton(text="↑", callback_data=up_cb)
        down_btn = InlineKeyboardButton(text="↓", callback_data=down_cb)
        rows.append([title_btn, toggle_btn, up_btn, down_btn])
    rows.append([InlineKeyboardButton(
        text="👁 Предпросмотр всей карточки",
        callback_data="sa:tpl:preview",
    )])
    rows.append([InlineKeyboardButton(
        text="↩️ Сбросить всё к стандартному",
        callback_data="sa:tpl:reset_all",
    )])
    raw_label = (
        "🛠 Расширенный HTML-режим (активен)"
        if raw_active else
        "🛠 Расширенный HTML-режим"
    )
    rows.append([InlineKeyboardButton(text=raw_label, callback_data="sa:tpl:raw:view")])
    rows.append([InlineKeyboardButton(text="← В супер-админку", callback_data="sa:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _render_blocks_view(call: CallbackQuery) -> None:
    blocks = await _load_blocks()
    raw_active = (await _raw_template()) is not None
    enabled_n = sum(1 for b in blocks if b.enabled)
    total_n = len(blocks)
    header = (
        "🎨 <b>Шаблон карточки магазина</b>\n"
        "Режим: <b>блочный конструктор</b>\n"
        f"Блоков активно: <b>{enabled_n}</b> из {total_n}\n\n"
    )
    if raw_active:
        header += (
            "⚠️ <b>Сейчас используется ручной HTML-шаблон</b>\n"
            "Изменения в блоках не повлияют на карточку, пока ручной "
            "шаблон не будет сброшен. Зайди в «Расширенный HTML-режим» "
            "и нажми «Сбросить ручной шаблон», чтобы вернуться к блокам.\n\n"
        )
    header += (
        "Нажми на название блока, чтобы изменить его текст. "
        "Галочка — включить/выключить, стрелки — переставить."
    )
    await safe_edit(call, header, _blocks_kb(blocks, raw_active))


@router.callback_query(F.data == "sa:tpl:view")
async def cb_view(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _render_blocks_view(call)
    await call.answer()


@router.callback_query(F.data == "sa:tpl:noop")
async def cb_noop(call: CallbackQuery) -> None:
    await call.answer()


@router.callback_query(F.data.startswith("sa:tpl:btoggle:"))
async def cb_block_toggle(call: CallbackQuery) -> None:
    block_id = call.data.split(":", 3)[3]
    blocks = await _load_blocks()
    if get_spec(block_id) is None:
        await call.answer("Неизвестный блок", show_alert=True)
        return
    new_enabled = toggle_block(blocks, block_id)
    await _save_blocks(blocks, by=call.from_user.id)
    await audit_write(
        call.from_user.id, "template.block.toggle", "block", block_id,
        payload={"enabled": new_enabled},
    )
    await _render_blocks_view(call)
    await call.answer("Включён" if new_enabled else "Выключен")


@router.callback_query(F.data.startswith("sa:tpl:bup:"))
async def cb_block_up(call: CallbackQuery) -> None:
    block_id = call.data.split(":", 3)[3]
    blocks = await _load_blocks()
    if not move_block(blocks, block_id, -1):
        await call.answer()
        return
    await _save_blocks(blocks, by=call.from_user.id)
    await audit_write(
        call.from_user.id, "template.block.move", "block", block_id,
        payload={"direction": "up"},
    )
    await _render_blocks_view(call)
    await call.answer()


@router.callback_query(F.data.startswith("sa:tpl:bdown:"))
async def cb_block_down(call: CallbackQuery) -> None:
    block_id = call.data.split(":", 3)[3]
    blocks = await _load_blocks()
    if not move_block(blocks, block_id, +1):
        await call.answer()
        return
    await _save_blocks(blocks, by=call.from_user.id)
    await audit_write(
        call.from_user.id, "template.block.move", "block", block_id,
        payload={"direction": "down"},
    )
    await _render_blocks_view(call)
    await call.answer()


def _block_editor_kb(spec, cfg: BlockConfig) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if spec.editable:
        rows.append([InlineKeyboardButton(
            text="✏️ Изменить текст блока",
            callback_data=f"sa:tpl:bedit:{cfg.id}",
        )])
    rows.append([InlineKeyboardButton(
        text="✅ Включён" if cfg.enabled else "⬜ Выключен",
        callback_data=f"sa:tpl:btoggle:{cfg.id}",
    )])
    rows.append([InlineKeyboardButton(
        text="↩️ Сбросить блок к стандартному",
        callback_data=f"sa:tpl:breset:{cfg.id}",
    )])
    rows.append([InlineKeyboardButton(text="👁 Предпросмотр", callback_data="sa:tpl:preview")])
    rows.append([InlineKeyboardButton(text="← К списку блоков", callback_data="sa:tpl:view")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _block_view_text(spec, cfg: BlockConfig) -> str:
    """Compose the text shown in the block editor screen."""
    current_text = cfg.custom_text if cfg.custom_text is not None else spec.default_text
    # «User-facing» version of the text — conditionals stripped to their bodies.
    user_view = simplify_for_user(current_text) if cfg.custom_text is None else current_text

    # Try to render this single block with a sample shop, so SA sees how it looks.
    try:
        rendered_block = render_with_template(
            _sample_shop(), date.today(), current_text, is_tracked=True,
        )
    except Exception as exc:
        rendered_block = f"⚠️ Ошибка рендера блока: {html.escape(str(exc))}"

    custom_mark = "пользовательский" if cfg.custom_text is not None else "стандартный"
    state_mark = "включён" if cfg.enabled else "выключен"

    vars_line = ""
    if spec.variables:
        vars_line = (
            "🧩 <b>Доступные подстановки в этом блоке:</b>\n"
            + ", ".join(f"<code>{{{v}}}</code>" for v in spec.variables)
            + "\n\n"
        )

    edit_hint = ""
    if spec.editable:
        edit_hint = (
            "💡 Чтобы изменить — нажми «Изменить текст». Пиши обычным "
            "сообщением. Жирный/курсив/ссылки — длинное нажатие на слово "
            "→ выбери стиль. Условные блоки <code>{?…}</code> писать нельзя."
        )
    else:
        edit_hint = "ℹ️ Этот блок нельзя редактировать (только включить/выключить)."

    return (
        f"✏️ <b>Блок:</b> {spec.title}\n"
        f"Состояние: <b>{state_mark}</b> · текст: <b>{custom_mark}</b>\n\n"
        f"👁 <b>Так выглядит блок сейчас</b> (на тестовом магазине):\n"
        f"{rendered_block}\n\n"
        f"📝 <b>Текущий текст блока:</b>\n"
        f"<pre>{html.escape(user_view)}</pre>\n\n"
        f"{vars_line}"
        f"{edit_hint}"
    )


@router.callback_query(F.data.startswith("sa:tpl:bview:"))
async def cb_block_view(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    block_id = call.data.split(":", 3)[3]
    spec = get_spec(block_id)
    if spec is None:
        await call.answer("Неизвестный блок", show_alert=True)
        return
    blocks = await _load_blocks()
    cfg = next((b for b in blocks if b.id == block_id), None)
    if cfg is None:
        await call.answer("Блок не найден", show_alert=True)
        return
    await safe_edit(call, _block_view_text(spec, cfg), _block_editor_kb(spec, cfg))
    await call.answer()


@router.callback_query(F.data.startswith("sa:tpl:bedit:"))
async def cb_block_edit(call: CallbackQuery, state: FSMContext) -> None:
    block_id = call.data.split(":", 3)[3]
    spec = get_spec(block_id)
    if spec is None or not spec.editable:
        await call.answer("Этот блок нельзя редактировать", show_alert=True)
        return
    await state.set_state(TplStates.block_waiting)
    await state.update_data(block_id=block_id)
    vars_hint = (
        ", ".join(f"<code>{{{v}}}</code>" for v in spec.variables)
        if spec.variables else "—"
    )
    await call.message.answer(
        f"✏️ <b>Изменение блока:</b> {spec.title}\n\n"
        "Пришли новый текст блока одним сообщением.\n\n"
        "💡 <b>Подсказки:</b>\n"
        "• Для выделения слов — длинное нажатие → жирный/курсив/ссылка/цитата. "
        "Бот сам сохранит форматирование.\n"
        f"• Доступные подстановки: {vars_hint}\n"
        "• Условные блоки <code>{?…}</code> писать нельзя — используй "
        "обычные <code>{переменная}</code>.\n\n"
        "Для отмены — /cancel."
    )
    await call.answer()


@router.message(TplStates.block_waiting, F.text == "/cancel")
async def msg_block_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено. Блок не изменился.")


@router.message(TplStates.block_waiting, F.text)
async def msg_block_apply(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    block_id = data.get("block_id")
    spec = get_spec(block_id) if isinstance(block_id, str) else None
    if spec is None or not spec.editable:
        await state.clear()
        await message.answer("Блок недоступен для редактирования.")
        return

    raw = message.html_text  # captures Telegram native formatting as HTML
    ok, err = validate_custom_text(raw)
    if not ok:
        await message.answer(f"❌ Текст блока не принят: {err}\n\nПопробуй ещё раз или /cancel.")
        return

    blocks = await _load_blocks()
    if not set_custom_text(blocks, block_id, raw):
        await state.clear()
        await message.answer("Блок не найден.")
        return

    # Sanity-check the assembled template renders without exceptions.
    assembled = assemble_blocks(blocks)
    ok_assembled, err_assembled = validate(assembled)
    if not ok_assembled:
        await message.answer(
            f"❌ Не удалось применить блок: {err_assembled}\n\n"
            "Попробуй другой текст или /cancel."
        )
        return
    try:
        rendered = render(assembled, _sample_ctx())
    except Exception as exc:
        await message.answer(
            f"❌ Не удалось отрендерить шаблон: {html.escape(str(exc))}\n\n"
            "Попробуй другой текст или /cancel."
        )
        return

    await _save_blocks(blocks, by=message.from_user.id)
    await audit_write(
        message.from_user.id, "template.block.edit", "block", block_id,
        payload={"len": len(raw)},
    )
    await state.clear()
    await message.answer(
        f"✅ Блок «{spec.title}» сохранён.\n\n"
        f"👁 <b>Так теперь выглядит вся карточка</b> (тестовый магазин):\n\n{rendered}"
    )


def _sample_ctx():
    """Build context for a sample shop — used for preview-on-save."""
    from services.card_template import build_context
    return build_context(_sample_shop(), date.today(), is_tracked=True)


@router.callback_query(F.data.startswith("sa:tpl:breset:"))
async def cb_block_reset(call: CallbackQuery) -> None:
    block_id = call.data.split(":", 3)[3]
    spec = get_spec(block_id)
    if spec is None:
        await call.answer("Неизвестный блок", show_alert=True)
        return
    blocks = await _load_blocks()
    reset_block(blocks, block_id)
    await _save_blocks(blocks, by=call.from_user.id)
    await audit_write(call.from_user.id, "template.block.reset", "block", block_id)
    cfg = next((b for b in blocks if b.id == block_id), None)
    if cfg is None:
        await _render_blocks_view(call)
        await call.answer("Сброшено")
        return
    await safe_edit(call, _block_view_text(spec, cfg), _block_editor_kb(spec, cfg))
    await call.answer("Блок сброшен к стандартному", show_alert=False)


@router.callback_query(F.data == "sa:tpl:reset_all")
async def cb_reset_all_ask(call: CallbackQuery) -> None:
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, сбросить всё", callback_data="sa:tpl:reset_all_yes")],
        [InlineKeyboardButton(text="← Отмена", callback_data="sa:tpl:view")],
    ])
    await safe_edit(
        call,
        "Сбросить все блоки к стандартным? Пользовательские тексты блоков "
        "и порядок будут потеряны.",
        kb,
    )
    await call.answer()


@router.callback_query(F.data == "sa:tpl:reset_all_yes")
async def cb_reset_all_yes(call: CallbackQuery) -> None:
    await cfg_set(
        CONFIG_KEY_BLOCKS, None, type_="str",
        description="Блоки шаблона карточки магазина",
        by=call.from_user.id,
    )
    await audit_write(call.from_user.id, "template.blocks.reset_all", "config", CONFIG_KEY_BLOCKS)
    await call.answer("Все блоки сброшены к стандартным", show_alert=True)
    await _render_blocks_view(call)


@router.callback_query(F.data == "sa:tpl:preview")
async def cb_preview(call: CallbackQuery) -> None:
    blocks = await _load_blocks()
    raw_active = (await _raw_template()) is not None
    if raw_active:
        # Preview the actually-rendered template (manual one).
        tpl = await _raw_template() or DEFAULT_TEMPLATE
    else:
        tpl = assemble_blocks(blocks) or DEFAULT_TEMPLATE
    try:
        rendered = render_with_template(_sample_shop(), date.today(), tpl, is_tracked=True)
    except Exception as exc:
        await safe_edit(
            call,
            f"❌ Ошибка рендера шаблона:\n<code>{html.escape(str(exc))}</code>",
            InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="← Назад", callback_data="sa:tpl:view"),
            ]]),
        )
        await call.answer()
        return
    note = ""
    if raw_active:
        note = "\n\n<i>(показан ручной HTML-шаблон)</i>"
    await safe_edit(
        call,
        f"👁 <b>Предпросмотр карточки</b> (тестовый магазин){note}\n\n{rendered}",
        InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="← Назад", callback_data="sa:tpl:view"),
        ]]),
    )
    await call.answer()


# =====================================================================
# RAW HTML MODE (legacy / advanced)
# =====================================================================


def _raw_view_kb(is_custom: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="✏️ Изменить вручную", callback_data="sa:tpl:raw:edit")],
        [InlineKeyboardButton(text="👁 Предпросмотр", callback_data="sa:tpl:raw:preview")],
        [InlineKeyboardButton(text="🧩 Список переменных", callback_data="sa:tpl:raw:vars")],
        [InlineKeyboardButton(text="📖 Подсказки и примеры", callback_data="sa:tpl:raw:help")],
        [InlineKeyboardButton(text="📦 Готовые шаблоны", callback_data="sa:tpl:raw:samples")],
    ]
    if is_custom:
        rows.append([InlineKeyboardButton(
            text="↩️ Сбросить ручной шаблон", callback_data="sa:tpl:raw:reset",
        )])
    rows.append([InlineKeyboardButton(text="← К блочному режиму", callback_data="sa:tpl:view")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _raw_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← Назад", callback_data="sa:tpl:raw:view")],
    ])


SAMPLE_MINIMAL = (
    "🏪 <b>{name}</b>\n"
    "{?chain:<i>{chain}</i>\n}"
    "📍 {address}\n"
    "{?price_today_line:\n{price_today_line}}\n"
    "{?description:\n{description}}"
)

SAMPLE_RICH = (
    "🏪 <b>{name}</b>{?chain: · <i>{chain}</i>}\n"
    "{sep}\n"
    "{?price_today_line:{price_today_line}\n}"
    "{?day_label:{day_label}\n}"
    "<blockquote>🚚 Завоз: <b>{next_arrival_date}</b> "
    "({days_to_arrival})\n"
    "{?price_start:В день завоза: <b>{price_start} ₽/кг</b>}</blockquote>\n"
    "{?today_event:⚡ Сегодня: {today_event}\n}"
    "{sep}\n"
    "📍 <a href=\"https://yandex.ru/maps/?text={address}\">{address}</a>\n"
    "{?description:\n{description}}\n"
    "{?is_tracked:\n⭐ <i>Ты отслеживаешь</i>}"
)


HELP_TEXT = (
    "📖 <b>Как писать шаблон карточки</b>\n\n"
    "<b>1. Подстановка переменной</b>\n"
    "Пишешь <code>{name}</code> — на её место подставляется значение. "
    "Если переменной нет или она пустая — будет пусто.\n\n"
    "<b>2. Условный блок «если переменная есть»</b>\n"
    "<code>{?chain:Сеть: {chain}}</code>\n"
    "Если у магазина указана сеть — покажется «Сеть: Megahand». "
    "Если не указана — блок исчезает целиком.\n\n"
    "<b>3. Условный блок «если переменной нет»</b>\n"
    "<code>{?!description:Описание не заполнено}</code>\n"
    "Восклицательный знак инвертирует условие.\n\n"
    "<b>4. Можно вкладывать</b>\n"
    "<code>{?price_start:В день завоза: {price_start} ₽}</code> — "
    "работает (условие + переменная внутри).\n\n"
    "<b>5. Форматирование (жирный, курсив, ссылки)</b>\n"
    "Когда пишешь шаблон в Telegram — выдели нужные слова и используй "
    "долгое нажатие. Там есть <b>Жирный</b>, <i>Курсив</i>, "
    "<u>Подчёркнутый</u>, <s>Зачёркнутый</s>, <code>Моно</code>, "
    "ссылка и цитата. Бот сам преобразует твоё форматирование. "
    "Ничего не нужно писать вручную тегами.\n\n"
    "<b>6. Карты-ссылки</b>\n"
    "Яндекс: <code>https://yandex.ru/maps/?text={address}</code>\n"
    "Google: <code>https://maps.google.com/?q={address}</code>"
)


SAMPLES_TEXT = (
    "📦 <b>Готовые шаблоны</b>\n\n"
    "Скопируй любой и пришли через «✏️ Изменить вручную» — увидишь "
    "результат в предпросмотре.\n\n"
    "<b>1️⃣ Минимальный</b> (только основное):\n"
    f"<pre>{html.escape(SAMPLE_MINIMAL)}</pre>\n\n"
    "<b>2️⃣ Богатый</b> (со ссылкой на Яндекс.Карты):\n"
    f"<pre>{html.escape(SAMPLE_RICH)}</pre>"
)


async def _render_raw_view(call: CallbackQuery) -> None:
    raw = await _raw_template()
    is_custom = raw is not None
    tpl = raw if raw else DEFAULT_TEMPLATE
    label = "пользовательский" if is_custom else "стандартный (берётся блочный)"
    warning = (
        ""
        if is_custom else
        "\n\n⚠️ <b>Сейчас активен блочный режим.</b> Если сохранишь ручной "
        "шаблон здесь — он перекроет блоки на рендере карточки."
    )
    text = (
        "🛠 <b>Расширенный режим — ручной HTML-шаблон</b>\n"
        f"Сейчас в bot_config: <b>{label}</b>\n\n"
        "Текущий шаблон (так выглядит исходник):\n"
        f"<pre>{html.escape(tpl)}</pre>\n\n"
        "Используй <code>{переменная}</code> для подстановки и "
        "<code>{?переменная:текст}</code> для условного блока. "
        "Список переменных — кнопка ниже."
        f"{warning}"
    )
    await safe_edit(call, text, _raw_view_kb(is_custom))


@router.callback_query(F.data == "sa:tpl:raw:view")
async def cb_raw_view(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _render_raw_view(call)
    await call.answer()


@router.callback_query(F.data == "sa:tpl:raw:vars")
async def cb_raw_vars(call: CallbackQuery) -> None:
    lines = ["🧩 <b>Доступные переменные</b>", ""]
    for name, descr in VARIABLES:
        lines.append(f"• <code>{{{name}}}</code> — {html.escape(descr)}")
    lines.append("")
    lines.append(
        "Условный блок: <code>{?name:текст}</code> — текст показывается, "
        "если переменная не пустая. <code>{?!name:текст}</code> — если пустая."
    )
    await safe_edit(call, "\n".join(lines), _raw_back_kb())
    await call.answer()


@router.callback_query(F.data == "sa:tpl:raw:help")
async def cb_raw_help(call: CallbackQuery) -> None:
    await safe_edit(call, HELP_TEXT, _raw_back_kb())
    await call.answer()


@router.callback_query(F.data == "sa:tpl:raw:samples")
async def cb_raw_samples(call: CallbackQuery) -> None:
    await safe_edit(call, SAMPLES_TEXT, _raw_back_kb())
    await call.answer()


@router.callback_query(F.data == "sa:tpl:raw:preview")
async def cb_raw_preview(call: CallbackQuery) -> None:
    raw = await _raw_template()
    tpl = raw if raw else DEFAULT_TEMPLATE
    try:
        rendered = render_with_template(_sample_shop(), date.today(), tpl, is_tracked=True)
    except Exception as exc:
        await safe_edit(
            call,
            f"❌ Ошибка рендера шаблона:\n<code>{html.escape(str(exc))}</code>",
            _raw_back_kb(),
        )
        await call.answer()
        return
    await safe_edit(
        call,
        f"👁 <b>Предпросмотр карточки</b> (тестовый магазин)\n\n{rendered}",
        _raw_back_kb(),
    )
    await call.answer()


@router.callback_query(F.data == "sa:tpl:raw:edit")
async def cb_raw_edit(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TplStates.raw_waiting)
    await call.message.answer(
        "✏️ <b>Редактирование ручного шаблона</b>\n\n"
        "Пришли новый шаблон одним сообщением.\n\n"
        "💡 <b>Как форматировать:</b>\n"
        "Пиши шаблон как обычное Telegram-сообщение. Чтобы что-то выделить — "
        "выдели слово, долгое нажатие → <b>Жирный</b>, <i>Курсив</i>, "
        "<u>Подчёркнутый</u>, <s>Зачёркнутый</s>, <code>Моноширинный</code>, "
        "ссылка или цитата. Бот сохранит твоё форматирование.\n\n"
        "🔧 <b>Подстановки и условия пиши обычным текстом:</b>\n"
        "• <code>{name}</code>, <code>{address}</code>, <code>{price_today_line}</code> и т.д.\n"
        "• <code>{?chain:текст если сеть указана}</code>\n"
        "• <code>{?!description:текст если описания нет}</code>\n\n"
        "Список переменных и примеры — в меню. Чтобы отменить — /cancel."
    )
    await call.answer()


@router.message(TplStates.raw_waiting, F.text == "/cancel")
async def msg_raw_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено. Ручной шаблон не изменился.")


@router.message(TplStates.raw_waiting, F.text)
async def msg_raw_apply(message: Message, state: FSMContext) -> None:
    raw = message.html_text
    ok, err = validate(raw)
    if not ok:
        await message.answer(
            f"❌ Шаблон не принят: {err}\n\nПопробуй ещё раз или /cancel."
        )
        return
    try:
        rendered = render_with_template(_sample_shop(), date.today(), raw, is_tracked=True)
    except Exception as exc:
        await message.answer(
            f"❌ Не удалось отрендерить шаблон: {html.escape(str(exc))}\n\n"
            "Попробуй ещё раз или /cancel."
        )
        return
    await cfg_set(
        CONFIG_KEY, raw, type_="str",
        description="Шаблон карточки магазина (ручной режим)",
        by=message.from_user.id,
    )
    await audit_write(message.from_user.id, "template.update", "config", CONFIG_KEY)
    await state.clear()
    await message.answer(
        f"✅ Ручной шаблон сохранён. Он перекрывает блочный режим на рендере.\n\n"
        f"👁 <b>Так увидит пользователь</b> (тестовый магазин):\n\n{rendered}"
    )


@router.callback_query(F.data == "sa:tpl:raw:reset")
async def cb_raw_reset_ask(call: CallbackQuery) -> None:
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, сбросить", callback_data="sa:tpl:raw:reset_yes")],
        [InlineKeyboardButton(text="← Отмена", callback_data="sa:tpl:raw:view")],
    ])
    await safe_edit(
        call,
        "Сбросить ручной HTML-шаблон? После сброса карточка снова "
        "соберётся из блоков (или дефолта, если блоки не настроены).",
        kb,
    )
    await call.answer()


@router.callback_query(F.data == "sa:tpl:raw:reset_yes")
async def cb_raw_reset_yes(call: CallbackQuery) -> None:
    await cfg_set(
        CONFIG_KEY, None, type_="str",
        description="Шаблон карточки магазина (ручной режим)",
        by=call.from_user.id,
    )
    await audit_write(call.from_user.id, "template.reset", "config", CONFIG_KEY)
    await call.answer("Ручной шаблон сброшен", show_alert=True)
    await _render_raw_view(call)
