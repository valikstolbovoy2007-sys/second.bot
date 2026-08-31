"""Визард создания магазина (только super_admin).

Шаги: name → address → working_hours → chain → cycle → anchor
       → price_start → price_step → description → photos → assign_admins
       → confirm.

На каждом шаге показывается подсказка на русском, объясняющая, что это
за поле, где оно увидится пользователю и можно ли его пропустить.
"""
import html
import logging
from datetime import date, datetime

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from data.repos.admin_roles import assign_shop, list_admins
from data.repos.shop_photos import add_photo
from data.repos.shops import create_shop, find_similar_shops
from handlers.admin.filters import IsSuperAdmin
from handlers.admin.ui import safe_edit
from keyboards.admin_kb import (
    AdminCb,
    anchor_picker_kb,
    chain_picker_kb,
    confirm_kb,
    cycle_picker_kb,
    dup_warning_kb,
    edit_menu_kb,
    photos_done_kb,
    skip_or_cancel_kb,
)
from services.audit import write as audit_write
from states.admin_states import AddShopStates

log = logging.getLogger(__name__)
router = Router(name="admin_add_shop")
router.message.filter(IsSuperAdmin())
router.callback_query.filter(IsSuperAdmin())

MAX_PRICE_START = 9_999
MAX_PRICE_STEP = 999
MAX_PHOTOS = 5
# Время работы — свободный текст. Лимит нужен только чтобы карточка
# не распухла; 100 символов хватает на «Пн-Сб 10:00–21:00, Вс выходной».
MAX_WORKING_HOURS = 100


# ---------- helper: step intros ----------

NAME_INTRO = (
    "🆕 <b>Создание магазина · Шаг 1/11</b>\n\n"
    "<b>Название магазина</b>\n"
    "Это то, что пользователь увидит первой строкой в каталоге и в карточке. "
    "Лучше короткое и узнаваемое — без сети в названии (сеть выбирается отдельно).\n\n"
    "Примеры: <i>«Центральный»</i>, <i>«У вокзала»</i>, <i>«На Ленина»</i>.\n\n"
    "Длина: 1–120 символов.\n"
    "✏️ Введи название одним сообщением:"
)

ADDRESS_INTRO = (
    "📍 <b>Шаг 2/11 · Адрес</b>\n\n"
    "Полный адрес одной строкой — пользователь увидит его в карточке и сможет "
    "по нему сориентироваться. Города/района добавляй, если магазин не один в твоём городе.\n\n"
    "Пример: <i>«ул. Ленина, 12, Минск»</i>.\n\n"
    "Длина: 1–200 символов.\n"
    "✏️ Введи адрес:"
)

WORKING_HOURS_INTRO = (
    "🕒 <b>Шаг 3/11 · Время работы</b>\n\n"
    "Когда магазин открыт. Выводится в карточке отдельной строкой — "
    "пользователь сразу видит, можно ли прямо сейчас идти.\n\n"
    "Формат — свободный текст, главное, чтобы человек понял. Примеры:\n"
    "• <i>«Пн-Сб 10:00–21:00, Вс выходной»</i>\n"
    "• <i>«Ежедневно 09:00–22:00»</i>\n"
    "• <i>«Будни 10–20, выходные 11–18»</i>\n\n"
    f"Длина: до {MAX_WORKING_HOURS} символов. Если расписания нет — «Пропустить».\n"
    "✏️ Введи время работы или нажми «Пропустить»:"
)

CHAIN_INTRO = (
    "🏷 <b>Шаг 4/11 · Сеть</b>\n\n"
    "К какой сети относится магазин. Сеть нужна, чтобы группировать одинаковые "
    "магазины и делать массовые операции (общий завоз, рассылка по всей сети).\n\n"
    "Если магазин одиночный — выбери «Независимый».\n\n"
    "👇 Выбери сеть:"
)

CYCLE_INTRO = (
    "🗓 <b>Шаг 5/11 · Длительность цикла</b>\n\n"
    "Через сколько дней магазин получает <b>новый завоз</b> и обнуляет цену. "
    "Это ключевой параметр — на нём строится вся логика уведомлений и скидок.\n\n"
    "• <b>14 дней</b> — стандартный цикл большинства секондов\n"
    "• <b>21 день</b> — если завозы реже\n"
    "• <b>Без цикла</b> — если магазин не работает по расписанию (просто адрес, без скидок по дням)\n\n"
    "👇 Выбери:"
)

ANCHOR_INTRO_TPL = (
    "📅 <b>Шаг 6/11 · Точка отсчёта (anchor)</b>\n\n"
    "Когда был <b>последний завоз</b>. От этой даты бот считает текущий день "
    "цикла и цену сегодня. Без неё бот не сможет сказать «через 3 дня завоз».\n\n"
    "Цикл: <b>{cycle_label}</b>\n\n"
    "• <b>Сегодня</b> — если завоз был сегодня\n"
    "• <b>Ввести вручную</b> — формат YYYY-MM-DD (например, 2026-04-22)\n"
    "• <b>Пропустить</b> — если точно не знаешь (карточка покажется без расписания, "
    "сможешь добавить позже)\n\n"
    "👇 Выбери:"
)

PRICE_START_INTRO = (
    "💰 <b>Шаг 7/11 · Цена в день завоза</b>\n\n"
    "Цена за <b>1 кг</b> в день, когда магазин получил свежий завоз — это "
    "<b>максимальная</b> цена цикла. По мере дней она будет уменьшаться по "
    "формуле «цена − шаг × день».\n\n"
    "Пример: 1200 (значит, в день завоза 1 кг = 1200 ₽).\n\n"
    "Если цены не знаешь — нажми «Пропустить». В карточке тогда не будет "
    "блока с ценой, всё остальное останется.\n\n"
    f"Лимит: 0–{MAX_PRICE_START} ₽/кг.\n"
    "✏️ Введи число или нажми «Пропустить»:"
)

PRICE_STEP_INTRO = (
    "📉 <b>Шаг 8/11 · Шаг снижения цены</b>\n\n"
    "На сколько ₽/кг цена падает <b>каждый день</b> цикла. Если в день завоза "
    "1200 ₽ и шаг 80, то на следующий день будет 1120, ещё через день 1040 и т.д.\n\n"
    "Часто шаг подбирают так, чтобы в последний день цикла цена была около нуля "
    "(например: цикл 14 дней, цена 1200 → шаг ≈ 85).\n\n"
    f"Лимит: 0–{MAX_PRICE_STEP} ₽/день.\n"
    "✏️ Введи число или нажми «Пропустить»:"
)

DESC_INTRO = (
    "📝 <b>Шаг 9/11 · Описание</b>\n\n"
    "Свободный текст — что особенного в этом магазине. Появится в карточке "
    "под блоком с расписанием.\n\n"
    "Хорошие примеры:\n"
    "• <i>«Большой ассортимент, есть детская секция»</i>\n"
    "• <i>«Самый дешёвый секонд в городе»</i>\n"
    "• <i>«Принимают вещи на комиссию»</i>\n\n"
    "Длина: до 2000 символов. Можно пропустить.\n"
    "✏️ Введи описание или нажми «Пропустить»:"
)

PHOTOS_INTRO = (
    "📷 <b>Шаг 10/11 · Фотографии магазина</b>\n\n"
    f"Можно загрузить до {MAX_PHOTOS} фотографий — пользователь увидит их в "
    "галерее карточки. Хорошо помогают опознать магазин снаружи и оценить ассортимент.\n\n"
    "Просто отправляй фото по одному (как обычное фото в Telegram). Когда закончишь — "
    "нажми «✅ Готово / дальше». Если фото нет — «Без фото».\n\n"
    "📷 Жду фотографий…"
)

ASSIGN_INTRO = (
    "👥 <b>Шаг 11/11 · Назначение админов</b>\n\n"
    "Кто из обычных админов сможет редактировать этот магазин и отвечать на "
    "фидбек по нему. <b>Ты как супер-админ всегда имеешь доступ</b>.\n\n"
    "Можно никого не назначать (магазин будет видно только тебе).\n"
    "Кликом по строке — добавляешь/убираешь админа.\n\n"
    "Когда выбрал — жми «➡️ Дальше»."
)


def _cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✖️ Отмена", callback_data=AdminCb(action="cancel").pack())
    ]])


# ---------- helpers: edit-mode → back to confirm ----------

async def _maybe_back_to_confirm_msg(message: Message, state: FSMContext) -> bool:
    """Если в edit-mode — отрисовать превью и вернуть True. Иначе False."""
    data = await state.get_data()
    if not data.get("editing"):
        return False
    await state.update_data(editing=None)
    await state.set_state(AddShopStates.confirm)
    await message.answer(_preview_text(data), reply_markup=confirm_kb("create"))
    return True


async def _maybe_back_to_confirm_call(call: CallbackQuery, state: FSMContext) -> bool:
    data = await state.get_data()
    if not data.get("editing"):
        return False
    await state.update_data(editing=None)
    await state.set_state(AddShopStates.confirm)
    await safe_edit(call, _preview_text(data), confirm_kb("create"))
    await call.answer()
    return True


# ---------- ENTRY ----------

@router.callback_query(F.data == "adm:addshop:start")
async def cb_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(AddShopStates.name)
    await call.message.answer(NAME_INTRO, reply_markup=_cancel_kb())
    await call.answer()


@router.callback_query(AdminCb.filter(F.action == "cancel"))
async def cb_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await safe_edit(call, "Отменено. Магазин не создан.", None)
    await call.answer()


@router.callback_query(AdminCb.filter(F.action == "restart"))
async def cb_restart(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(AddShopStates.name)
    await call.message.answer(NAME_INTRO, reply_markup=_cancel_kb())
    await call.answer("Начали заново")


# ---------- name ----------

@router.message(AddShopStates.name, F.text)
async def msg_name(message: Message, state: FSMContext) -> None:
    name = message.text.strip()
    if not (1 <= len(name) <= 120):
        await message.answer("⚠️ Длина 1–120 символов. Повтори.")
        return
    await state.update_data(name=name)
    if await _maybe_back_to_confirm_msg(message, state):
        return
    await state.set_state(AddShopStates.address)
    await message.answer(ADDRESS_INTRO, reply_markup=_cancel_kb())


# ---------- address ----------

@router.message(AddShopStates.address, F.text)
async def msg_address(message: Message, state: FSMContext) -> None:
    addr = message.text.strip()
    if not (1 <= len(addr) <= 200):
        await message.answer("⚠️ Длина 1–200 символов. Повтори.")
        return
    await state.update_data(address=addr)
    if await _maybe_back_to_confirm_msg(message, state):
        return
    await state.set_state(AddShopStates.working_hours)
    await message.answer(WORKING_HOURS_INTRO, reply_markup=skip_or_cancel_kb())


# ---------- working hours ----------

@router.callback_query(AddShopStates.working_hours, AdminCb.filter(F.action == "skip"))
async def cb_working_hours_skip(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(working_hours=None)
    if await _maybe_back_to_confirm_call(call, state):
        return
    await state.set_state(AddShopStates.chain)
    await safe_edit(call, CHAIN_INTRO, chain_picker_kb())
    await call.answer()


@router.message(AddShopStates.working_hours, F.text)
async def msg_working_hours(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    if len(text) > MAX_WORKING_HOURS:
        await message.answer(
            f"⚠️ Слишком длинно (макс {MAX_WORKING_HOURS}). Сократи и повтори."
        )
        return
    # Пустую строку трактуем как «нет данных», чтобы не пихать "" в БД.
    await state.update_data(working_hours=text or None)
    if await _maybe_back_to_confirm_msg(message, state):
        return
    await state.set_state(AddShopStates.chain)
    await message.answer(CHAIN_INTRO, reply_markup=chain_picker_kb())


# ---------- chain ----------

@router.callback_query(AddShopStates.chain, AdminCb.filter(F.action == "chain"))
async def cb_chain(call: CallbackQuery, callback_data: AdminCb, state: FSMContext) -> None:
    chain = callback_data.value or None
    await state.update_data(chain_name=chain)
    if await _maybe_back_to_confirm_call(call, state):
        return
    await state.set_state(AddShopStates.cycle)
    await safe_edit(call, CYCLE_INTRO, cycle_picker_kb())
    await call.answer()


# ---------- cycle ----------

@router.callback_query(AddShopStates.cycle, AdminCb.filter(F.action == "cycle"))
async def cb_cycle(call: CallbackQuery, callback_data: AdminCb, state: FSMContext) -> None:
    try:
        n = int(callback_data.value)
    except ValueError:
        await call.answer("Битый ввод", show_alert=True)
        return
    cycle = n if n > 0 else None
    await state.update_data(cycle_length=cycle)
    if await _maybe_back_to_confirm_call(call, state):
        return
    await state.set_state(AddShopStates.anchor)
    cycle_label = f"{cycle} дн." if cycle else "без цикла"
    await safe_edit(call, ANCHOR_INTRO_TPL.format(cycle_label=cycle_label), anchor_picker_kb())
    await call.answer()


# ---------- anchor ----------

@router.callback_query(AddShopStates.anchor, AdminCb.filter(F.action == "anchor_today"))
async def cb_anchor_today(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(anchor_date=date.today().isoformat())
    if await _maybe_back_to_confirm_call(call, state):
        return
    await _go_price_start(call, state)


@router.callback_query(AddShopStates.anchor, AdminCb.filter(F.action == "anchor_skip"))
async def cb_anchor_skip(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(anchor_date=None)
    if await _maybe_back_to_confirm_call(call, state):
        return
    await _go_price_start(call, state)


@router.callback_query(AddShopStates.anchor, AdminCb.filter(F.action == "anchor_manual"))
async def cb_anchor_manual(call: CallbackQuery, state: FSMContext) -> None:
    await call.message.answer(
        "Введи дату последнего завоза в формате <b>YYYY-MM-DD</b> (например, 2026-04-22):",
        reply_markup=_cancel_kb(),
    )
    await call.answer()


@router.message(AddShopStates.anchor, F.text)
async def msg_anchor_input(message: Message, state: FSMContext) -> None:
    try:
        d = datetime.strptime(message.text.strip(), "%Y-%m-%d").date()
    except ValueError:
        await message.answer("⚠️ Формат YYYY-MM-DD. Повтори (например, 2026-04-22).")
        return
    if d > date.today():
        await message.answer("⚠️ Дата завоза не может быть в будущем. Повтори.")
        return
    await state.update_data(anchor_date=d.isoformat())
    if await _maybe_back_to_confirm_msg(message, state):
        return
    await state.set_state(AddShopStates.price_start)
    await message.answer(PRICE_START_INTRO, reply_markup=skip_or_cancel_kb())


async def _go_price_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddShopStates.price_start)
    await safe_edit(call, PRICE_START_INTRO, skip_or_cancel_kb())
    await call.answer()


# ---------- price_start ----------

@router.callback_query(AddShopStates.price_start, AdminCb.filter(F.action == "skip"))
async def cb_price_start_skip(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(price_start=None, price_step=None)
    if await _maybe_back_to_confirm_call(call, state):
        return
    await state.set_state(AddShopStates.description)
    await safe_edit(call, DESC_INTRO, skip_or_cancel_kb())
    await call.answer()


@router.message(AddShopStates.price_start, F.text)
async def msg_price_start(message: Message, state: FSMContext) -> None:
    raw = message.text.strip()
    try:
        v = int(raw)
    except ValueError:
        await message.answer("⚠️ Это должно быть целое число. Повтори (например, 1200).")
        return
    if not (0 <= v <= MAX_PRICE_START):
        await message.answer(f"⚠️ Допустимо 0–{MAX_PRICE_START}. Повтори.")
        return
    await state.update_data(price_start=v)
    # В edit-mode для price_start: если шаг ещё не выставлен, попросим и его
    data = await state.get_data()
    if data.get("editing") and data.get("price_step") is not None:
        # Шаг уже есть — возвращаем в превью
        if await _maybe_back_to_confirm_msg(message, state):
            return
    await state.set_state(AddShopStates.price_step)
    await message.answer(PRICE_STEP_INTRO, reply_markup=skip_or_cancel_kb())


# ---------- price_step ----------

@router.callback_query(AddShopStates.price_step, AdminCb.filter(F.action == "skip"))
async def cb_price_step_skip(call: CallbackQuery, state: FSMContext) -> None:
    # Без шага считать цену нельзя — сбрасываем оба
    await state.update_data(price_start=None, price_step=None)
    if await _maybe_back_to_confirm_call(call, state):
        return
    await state.set_state(AddShopStates.description)
    await safe_edit(
        call,
        "ℹ️ Без шага цена не считается, оба поля сброшены — добавишь позже.\n\n" + DESC_INTRO,
        skip_or_cancel_kb(),
    )
    await call.answer()


@router.message(AddShopStates.price_step, F.text)
async def msg_price_step(message: Message, state: FSMContext) -> None:
    raw = message.text.strip()
    try:
        v = int(raw)
    except ValueError:
        await message.answer("⚠️ Целое число. Повтори (например, 80).")
        return
    if not (0 <= v <= MAX_PRICE_STEP):
        await message.answer(f"⚠️ Допустимо 0–{MAX_PRICE_STEP}. Повтори.")
        return
    await state.update_data(price_step=v)
    if await _maybe_back_to_confirm_msg(message, state):
        return
    await state.set_state(AddShopStates.description)
    await message.answer(DESC_INTRO, reply_markup=skip_or_cancel_kb())


# ---------- description ----------

@router.callback_query(AddShopStates.description, AdminCb.filter(F.action == "skip"))
async def cb_desc_skip(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(description=None)
    if await _maybe_back_to_confirm_call(call, state):
        return
    await _go_photos(call, state)


@router.message(AddShopStates.description, F.text)
async def msg_description(message: Message, state: FSMContext) -> None:
    desc = message.text.strip()
    if len(desc) > 2000:
        await message.answer("⚠️ Слишком длинно (макс 2000). Сократи и повтори.")
        return
    await state.update_data(description=desc)
    if await _maybe_back_to_confirm_msg(message, state):
        return
    await _go_photos_msg(message, state)


# ---------- photos ----------

async def _go_photos(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(photo_file_ids=[])
    await state.set_state(AddShopStates.photos)
    await safe_edit(call, PHOTOS_INTRO, photos_done_kb())
    await call.answer()


async def _go_photos_msg(message: Message, state: FSMContext) -> None:
    await state.update_data(photo_file_ids=[])
    await state.set_state(AddShopStates.photos)
    await message.answer(PHOTOS_INTRO, reply_markup=photos_done_kb())


@router.message(AddShopStates.photos, F.photo)
async def msg_photo(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    photos: list[str] = list(data.get("photo_file_ids") or [])
    if len(photos) >= MAX_PHOTOS:
        await message.answer(f"⚠️ Достигнут лимит ({MAX_PHOTOS}). Жми «Готово / дальше».")
        return
    photos.append(message.photo[-1].file_id)
    await state.update_data(photo_file_ids=photos)
    await message.answer(
        f"✅ Загружено {len(photos)}/{MAX_PHOTOS}. "
        f"{'Можешь прислать ещё или жми «Готово».' if len(photos) < MAX_PHOTOS else 'Лимит. Жми «Готово».'}",
        reply_markup=photos_done_kb(),
    )


@router.message(AddShopStates.photos, F.text)
async def msg_photo_wrong_text(message: Message, state: FSMContext) -> None:
    await message.answer(
        "Тут жду фотографии (как обычное фото в Telegram), а не текст.\n"
        "Если фото не нужны — жми «Без фото» на клавиатуре выше.",
        reply_markup=photos_done_kb(),
    )


@router.callback_query(AddShopStates.photos, AdminCb.filter(F.action == "photos_skip"))
async def cb_photos_skip(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(photo_file_ids=[])
    if await _maybe_back_to_confirm_call(call, state):
        return
    await _go_assign(call, state)


@router.callback_query(AddShopStates.photos, AdminCb.filter(F.action == "photos_done"))
async def cb_photos_done(call: CallbackQuery, state: FSMContext) -> None:
    if await _maybe_back_to_confirm_call(call, state):
        return
    await _go_assign(call, state)


# ---------- assign admins ----------

async def _render_assign_kb(selected: set[int]) -> InlineKeyboardMarkup:
    admins = [a for a in await list_admins() if a.role == "admin"]
    rows: list[list[InlineKeyboardButton]] = []
    for a in admins:
        mark = "✅" if a.tg_id in selected else "▫️"
        label = f"{mark} {a.tg_id}"
        if a.note:
            label += f" • {html.escape(a.note)[:30]}"
        rows.append([InlineKeyboardButton(
            text=label[:60],
            callback_data=AdminCb(action="assigntog", value=str(a.tg_id)).pack(),
        )])
    rows.append([InlineKeyboardButton(
        text="➡️ Дальше", callback_data=AdminCb(action="assigndone").pack(),
    )])
    rows.append([InlineKeyboardButton(text="✖️ Отмена", callback_data=AdminCb(action="cancel").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _go_assign(call: CallbackQuery, state: FSMContext) -> None:
    admins = [a for a in await list_admins() if a.role == "admin"]
    if not admins:
        await state.update_data(assigned_admin_ids=[])
        await _go_confirm(call, state)
        return
    await state.update_data(assigned_admin_ids=[])
    await state.set_state(AddShopStates.assign_admins)
    kb = await _render_assign_kb(set())
    await safe_edit(call, ASSIGN_INTRO, kb)
    await call.answer()


@router.callback_query(AddShopStates.assign_admins, AdminCb.filter(F.action == "assigntog"))
async def cb_assign_toggle(call: CallbackQuery, callback_data: AdminCb, state: FSMContext) -> None:
    try:
        tg_id = int(callback_data.value)
    except (TypeError, ValueError):
        await call.answer("Битый ввод", show_alert=True)
        return
    data = await state.get_data()
    selected: set[int] = set(data.get("assigned_admin_ids") or [])
    if tg_id in selected:
        selected.remove(tg_id)
    else:
        selected.add(tg_id)
    await state.update_data(assigned_admin_ids=list(selected))
    kb = await _render_assign_kb(selected)
    await safe_edit(call, f"{ASSIGN_INTRO}\n\n<i>Выбрано: {len(selected)}</i>", kb)
    await call.answer()


@router.callback_query(AddShopStates.assign_admins, AdminCb.filter(F.action == "assigndone"))
async def cb_assign_done(call: CallbackQuery, state: FSMContext) -> None:
    if await _maybe_back_to_confirm_call(call, state):
        return
    await _go_confirm(call, state)


# ---------- confirm + duplicate check ----------

def _preview_text(data: dict) -> str:
    chain = data.get("chain_name") or "Независимый"
    cycle = data.get("cycle_length")
    anchor = data.get("anchor_date") or "—"
    desc = data.get("description")
    hours = data.get("working_hours")
    cycle_label = f"{cycle} дн." if cycle else "—"
    assigned: list[int] = list(data.get("assigned_admin_ids") or [])
    assigned_label = ", ".join(str(t) for t in assigned) if assigned else "—"
    photos: list[str] = list(data.get("photo_file_ids") or [])
    price_start = data.get("price_start")
    price_step = data.get("price_step")
    price_label = (
        f"{price_start} ₽/кг, шаг {price_step} ₽/день"
        if price_start is not None and price_step is not None else "—"
    )
    return (
        "🔎 <b>Проверь данные перед созданием</b>\n\n"
        f"🛍 <b>{html.escape(data.get('name', ''))}</b>\n"
        f"📍 {html.escape(data.get('address', ''))}\n"
        f"🕒 Время работы: {html.escape(hours) if hours else '—'}\n"
        f"🏷 Сеть: {html.escape(str(chain))}\n"
        f"🗓 Цикл: {cycle_label}\n"
        f"📅 Последний завоз: {anchor}\n"
        f"💰 Цена: {price_label}\n"
        f"📝 Описание: {html.escape(desc) if desc else '—'}\n"
        f"📷 Фото: {len(photos)} шт.\n"
        f"👥 Админы: {assigned_label}\n\n"
        "Если всё ок — жми «✅ Создать». Если что-то не так — «✏️ Начать заново»."
    )


async def _go_confirm(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    # дубль-чек
    similar = await find_similar_shops(data.get("name", ""), data.get("address", ""))
    if similar:
        lines = ["⚠️ <b>Похожий магазин уже существует</b>\n"]
        for s in similar:
            chain_p = f"[{s.chain_name}] " if s.chain_name else ""
            flag = " 🚫" if not s.is_active else ""
            lines.append(f"  • {chain_p}{html.escape(s.name)} — {html.escape(s.address)}{flag}")
        lines.append("")
        lines.append(_preview_text(data))
        await state.set_state(AddShopStates.confirm)
        await safe_edit(call, "\n".join(lines), dup_warning_kb())
        await call.answer()
        return
    await state.set_state(AddShopStates.confirm)
    await safe_edit(call, _preview_text(data), confirm_kb("create"))
    await call.answer()


@router.callback_query(AddShopStates.confirm, AdminCb.filter(F.action == "dup_force"))
async def cb_dup_force(call: CallbackQuery, state: FSMContext) -> None:
    """Пользователь подтвердил создание несмотря на дубль-предупреждение."""
    data = await state.get_data()
    await safe_edit(call, _preview_text(data), confirm_kb("create"))
    await call.answer()


# ---------- edit-field menu ----------

@router.callback_query(AddShopStates.confirm, AdminCb.filter(F.action == "edit_menu"))
async def cb_edit_menu(call: CallbackQuery, state: FSMContext) -> None:
    await safe_edit(
        call,
        "✏️ <b>Что меняем?</b>\nКликни поле — попросит ввести его заново, "
        "потом вернёт обратно к превью.",
        edit_menu_kb(),
    )
    await call.answer()


@router.callback_query(AddShopStates.confirm, AdminCb.filter(F.action == "back_to_confirm"))
async def cb_back_to_confirm(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await safe_edit(call, _preview_text(data), confirm_kb("create"))
    await call.answer()


@router.callback_query(AddShopStates.confirm, AdminCb.filter(F.action == "edit"))
async def cb_edit_field(call: CallbackQuery, callback_data: AdminCb, state: FSMContext) -> None:
    field = callback_data.value
    await state.update_data(editing=True)

    if field == "name":
        await state.set_state(AddShopStates.name)
        await call.message.answer(NAME_INTRO, reply_markup=_cancel_kb())
    elif field == "address":
        await state.set_state(AddShopStates.address)
        await call.message.answer(ADDRESS_INTRO, reply_markup=_cancel_kb())
    elif field == "working_hours":
        await state.set_state(AddShopStates.working_hours)
        await call.message.answer(WORKING_HOURS_INTRO, reply_markup=skip_or_cancel_kb())
    elif field == "chain":
        await state.set_state(AddShopStates.chain)
        await call.message.answer(CHAIN_INTRO, reply_markup=chain_picker_kb())
    elif field == "cycle":
        await state.set_state(AddShopStates.cycle)
        await call.message.answer(CYCLE_INTRO, reply_markup=cycle_picker_kb())
    elif field == "anchor":
        cycle = (await state.get_data()).get("cycle_length")
        cycle_label = f"{cycle} дн." if cycle else "без цикла"
        await state.set_state(AddShopStates.anchor)
        await call.message.answer(
            ANCHOR_INTRO_TPL.format(cycle_label=cycle_label),
            reply_markup=anchor_picker_kb(),
        )
    elif field == "price_start":
        await state.set_state(AddShopStates.price_start)
        await call.message.answer(PRICE_START_INTRO, reply_markup=skip_or_cancel_kb())
    elif field == "price_step":
        # шаг без цены смысла не имеет — если price_start пустой, перебросим на price_start
        if (await state.get_data()).get("price_start") is None:
            await call.answer("Сначала заполни цену в день завоза", show_alert=True)
            await state.set_state(AddShopStates.price_start)
            await call.message.answer(PRICE_START_INTRO, reply_markup=skip_or_cancel_kb())
            return
        await state.set_state(AddShopStates.price_step)
        await call.message.answer(PRICE_STEP_INTRO, reply_markup=skip_or_cancel_kb())
    elif field == "description":
        await state.set_state(AddShopStates.description)
        await call.message.answer(DESC_INTRO, reply_markup=skip_or_cancel_kb())
    elif field == "photos":
        await state.update_data(photo_file_ids=[])
        await state.set_state(AddShopStates.photos)
        await call.message.answer(
            "📷 Старые фото сброшены. " + PHOTOS_INTRO,
            reply_markup=photos_done_kb(),
        )
    elif field == "assign":
        await state.update_data(assigned_admin_ids=[])
        await state.set_state(AddShopStates.assign_admins)
        kb = await _render_assign_kb(set())
        await call.message.answer(ASSIGN_INTRO, reply_markup=kb)
    else:
        await call.answer("Неизвестное поле", show_alert=True)
        return
    await call.answer()


@router.callback_query(AddShopStates.confirm, AdminCb.filter(F.action == "create_yes"))
async def cb_create_yes(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    anchor_str = data.get("anchor_date")
    anchor = datetime.strptime(anchor_str, "%Y-%m-%d").date() if anchor_str else None
    try:
        shop_id = await create_shop(
            name=data["name"],
            address=data["address"],
            description=data.get("description"),
            chain_name=data.get("chain_name"),
            cycle_length=data.get("cycle_length"),
            anchor_date=anchor,
            price_start=data.get("price_start"),
            price_step=data.get("price_step"),
            working_hours=data.get("working_hours"),
        )
    except Exception as e:
        log.exception("create_shop failed")
        # FSM не сбрасываем — превью остаётся, пользователь может починить и повторить
        err = html.escape(str(e))[:300]
        await safe_edit(
            call,
            f"❌ <b>Ошибка создания:</b>\n<code>{err}</code>\n\n"
            f"Превью остался — можно «✏️ Изменить поле» и попробовать снова.\n\n"
            + _preview_text(data),
            confirm_kb("create"),
        )
        await call.answer("Ошибка — см. сообщение", show_alert=True)
        return

    payload = {k: v for k, v in data.items() if k not in ("anchor_date", "photo_file_ids")}
    payload["anchor_date"] = anchor_str
    await audit_write(call.from_user.id, "shop.create", "shop", shop_id, payload)

    # photos
    photos: list[str] = list(data.get("photo_file_ids") or [])
    if photos:
        for fid in photos:
            try:
                await add_photo(shop_id, fid, call.from_user.id)
            except Exception:
                log.exception("photo add failed during create_shop")
        await audit_write(
            call.from_user.id, "shop.photo.add", "shop", shop_id,
            {"count": len(photos), "via": "create_shop"},
        )

    # assign admins
    assigned: list[int] = list(data.get("assigned_admin_ids") or [])
    if assigned:
        for tg in assigned:
            await assign_shop(shop_id, tg, call.from_user.id)
        await audit_write(
            call.from_user.id, "shop.assign_admins", "shop", shop_id,
            {"added": assigned, "removed": []},
        )
    await state.clear()

    summary_lines = [f"✅ Создан магазин <b>{html.escape(data['name'])}</b> (id={shop_id})."]
    if photos:
        summary_lines.append(f"📷 Загружено фото: {len(photos)}")
    if assigned:
        summary_lines.append(f"👥 Назначен админам: {', '.join(str(t) for t in assigned)}")

    # лениво импортируем, чтобы избежать кругового импорта
    from handlers.admin.shops import ShopCb
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🔍 Открыть карточку",
            callback_data=ShopCb(action="card", shop_id=shop_id, page=0).pack(),
        )],
        [InlineKeyboardButton(text="🛍 К списку магазинов",
                              callback_data=ShopCb(action="list", page=0).pack())],
        [InlineKeyboardButton(text="← В админку", callback_data="adm:back")],
    ])
    await safe_edit(call, "\n".join(summary_lines), kb)
    await call.answer()
