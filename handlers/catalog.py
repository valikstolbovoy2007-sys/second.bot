import html
import logging
from datetime import date

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from data.repos.shops import Shop, get_shop, list_active_shops
from data.repos.subs import is_subscribed, subscribed_shop_ids
from data.repos.users import upsert_user
from keyboards.catalog_kb import (
    FILTER_SHORT,
    PAGE_SIZE,
    SORT_LABELS,
    CatalogCb,
    catalog_kb,
    empty_filter_kb,
    more_filters_kb,
    search_cancel_kb,
    shop_card_kb,
    sort_kb,
)
from services.card_render import format_price_schedule, phase_marker
from services.card_view import show_shop_card, show_text_view
from services.chat_journal import journal
from services.chat_render import render
from services.maps import yandex_maps_url
from services.workspace import ws
from services.catalog import (
    FLT_ALL,
    SORT_NAME,
    SORT_NEARBY,
    VALID_FILTERS,
    VALID_SORTS,
    apply,
    shop_distance_km,
)
from services.texts import t
from states.catalog_states import CatalogStates

log = logging.getLogger(__name__)
router = Router(name="catalog")

# In-memory map: telegram user_id → active search query.
# Ephemeral by design — search is cleared on bot restart, which is fine.
_user_search: dict[int, str] = {}
_MAX_SEARCH_LEN = 60

# In-memory map: telegram user_id → последние переданные координаты (lat, lon).
# Использует только sort=SORT_NEARBY; локацию переспрашиваем при каждой
# активации сортировки (см. cb_sort_pick), между активациями точка живёт в памяти.
_user_location: dict[int, tuple[float, float]] = {}


def _phase_markers(shops: list[Shop], today: date) -> dict[int, str]:
    return {s.id: m for s in shops if (m := phase_marker(s, today))}


def _norm_flt(flt: str) -> str:
    return flt if flt in VALID_FILTERS else FLT_ALL


def _norm_sort(sort: str) -> str:
    return sort if sort in VALID_SORTS else SORT_NAME


async def _build_header(
    *, total: int, flt: str, sort: str, search: str,
) -> str:
    parts: list[str] = []
    if search:
        parts.append(f'🔍 <b>Результаты по поиску:</b> «{html.escape(search)}»')
    else:
        parts.append(await t("catalog.title"))
    parts.append(await t("catalog.found", count=total))
    chips: list[str] = []
    if flt in FILTER_SHORT:
        chips.append(FILTER_SHORT[flt])
    if sort != SORT_NAME:
        chips.append(f"↕ {SORT_LABELS[sort]}")
    if chips:
        parts.append(" · ".join(chips))
    parts.append("")
    parts.append(await t("catalog.legend"))
    parts.append("")
    parts.append(await t("catalog.hint"))
    return "\n".join(parts)


async def _catalog_payload(
    user_id: int, tg_id: int, *, page: int, flt: str, sort: str,
    point: tuple[float, float] | None = None,
) -> tuple[str, InlineKeyboardMarkup]:
    """(текст, клавиатура) экрана списка каталога (включая пустой случай).

    user_id — id юзера в БД (для подписок), tg_id — телеграм-id (ключ
    активного поиска, т.к. поиск хранится в памяти под tg-id).

    `point` — (lat, lon) для сортировки SORT_NEARBY; без точки магазины
    без координат остаются в конце списка.
    """
    flt = _norm_flt(flt)
    sort = _norm_sort(sort)
    search = _user_search.get(tg_id, "")
    today = date.today()

    all_shops = await list_active_shops()
    sub_ids = await subscribed_shop_ids(user_id)
    filtered = apply(
        all_shops, today,
        flt=flt, sort=sort, search=search,
        subscribed_ids=sub_ids,
        point=point,
    )

    total = len(filtered)
    if total == 0:
        body = await t(
            "catalog.empty_filter" if (flt != FLT_ALL or search) else "catalog.empty"
        )
        return body, empty_filter_kb(flt, has_search=bool(search))

    last_page = max(0, (total - 1) // PAGE_SIZE)
    page = max(0, min(page, last_page))
    page_shops = filtered[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    markers = _phase_markers(page_shops, today)

    distances: dict[int, float] | None = None
    if sort == SORT_NEARBY and point is not None:
        distances = {
            s.id: d for s in page_shops
            if (d := shop_distance_km(s, point)) is not None
        }

    body = await _build_header(total=total, flt=flt, sort=sort, search=search)
    kb = catalog_kb(
        page_shops, page, flt, sort, total,
        has_search=bool(search),
        phase_markers=markers,
        tracked_ids=sub_ids,
        distances=distances,
    )
    return body, kb


async def _render_catalog(
    call: CallbackQuery,
    *,
    page: int,
    flt: str,
    sort: str,
    state: FSMContext | None = None,
) -> None:
    if sort == SORT_NEARBY:
        point = _point_for(call.from_user.id)
        if point is None:
            await _ask_location(call, flt=flt, sort=sort, state=state)
            return
    else:
        point = None
    user_id = await upsert_user(call.from_user.id, call.from_user.username)
    body, kb = await _catalog_payload(
        user_id, tg_id=call.from_user.id, page=page, flt=flt, sort=sort,
        point=point,
    )
    await show_text_view(call, body, kb)
    await call.answer()


async def _close_card_back_to_catalog(
    call: CallbackQuery, user_id: int, *, page: int, flt: str, sort: str,
    state: FSMContext | None = None,
) -> None:
    """«Назад» с карточки: список появляется, затем карточка удаляется."""
    if sort == SORT_NEARBY:
        point = _point_for(call.from_user.id)
        if point is None:
            await _ask_location(call, flt=flt, sort=sort, state=state)
            return
    else:
        point = None
    ws.close_card(user_id)
    body, kb = await _catalog_payload(
        user_id, tg_id=call.from_user.id, page=page, flt=flt, sort=sort,
        point=point,
    )
    sent = await call.bot.send_message(
        call.message.chat.id, body,
        reply_markup=kb, disable_web_page_preview=True,
    )
    ws.set_active(user_id, sent.message_id)
    try:
        await call.message.delete()
    except TelegramBadRequest:
        pass
    await call.answer()


# ---------- entry points ----------


def _point_for(tg_id: int) -> tuple[float, float] | None:
    return _user_location.get(tg_id)


async def _delete_messages(bot, chat_id: int, msg_ids: list[int | None]) -> None:
    for msg_id in msg_ids:
        if msg_id is None:
            continue
        try:
            await bot.delete_message(chat_id, msg_id)
        except TelegramBadRequest:
            pass
        journal.remove(chat_id, msg_id)


async def _ask_location(
    ctx: CallbackQuery | Message, *, flt: str, sort: str, state: FSMContext | None,
) -> None:
    """Перевести каталог в состояние запроса геолокации.

    Активный экран (список/карточка) удаляется, вместо него — промпт с
    reply-клавиатурой: «Поделиться местоположением» (гео можно запросить
    только reply-кнопкой) и «👈 Назад». После ответа пользователя каталог
    рисуется заново (см. msg_catalog_location).
    """
    await state.set_state(CatalogStates.locating)
    back_label = await t("catalog.nearby.back")
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=await t("catalog.nearby.button"), request_location=True)],
            [KeyboardButton(text=back_label)],
        ],
        resize_keyboard=True,
    )
    msg = ctx.message if isinstance(ctx, CallbackQuery) else ctx
    user_id = await upsert_user(ctx.from_user.id, ctx.from_user.username)
    sent = await msg.answer(await t("catalog.nearby.prompt"), reply_markup=kb)
    ws.set_active(user_id, sent.message_id)
    await state.update_data(cat_flt=flt, cat_sort=sort, loc_msg_id=sent.message_id)
    if isinstance(ctx, CallbackQuery):
        await _delete_messages(ctx.bot, msg.chat.id, [msg.message_id])
        await ctx.answer()


@router.callback_query(F.data == "catalog:open")
async def cb_open(call: CallbackQuery) -> None:
    # Явное открытие каталога из главного меню — всегда «свежий» каталог:
    # старый поиск сбрасываем, чтобы не тащить его в новые заходы.
    _user_search.pop(call.from_user.id, None)
    await _render_catalog(call, page=0, flt=FLT_ALL, sort=SORT_NAME)


@router.callback_query(CatalogCb.filter(F.action == "list"))
async def cb_list(call: CallbackQuery, callback_data: CatalogCb, state: FSMContext) -> None:
    user_id = await upsert_user(call.from_user.id, call.from_user.username)
    if ws.card(user_id) == call.message.message_id:
        # Пришли «Назад» с открытой карточки — закрываем её, список остаётся.
        await _close_card_back_to_catalog(
            call, user_id,
            page=callback_data.page, flt=callback_data.flt, sort=callback_data.sort,
            state=state,
        )
        return
    await _render_catalog(
        call, page=callback_data.page, flt=callback_data.flt, sort=callback_data.sort,
        state=state,
    )


@router.callback_query(CatalogCb.filter(F.action == "filter"))
async def cb_filter(call: CallbackQuery, callback_data: CatalogCb, state: FSMContext) -> None:
    await _render_catalog(call, page=0, flt=callback_data.flt, sort=callback_data.sort, state=state)


# ---------- shop card / schedule ----------


@router.callback_query(CatalogCb.filter(F.action == "shop"))
async def cb_shop(call: CallbackQuery, callback_data: CatalogCb) -> None:
    user_id = await upsert_user(call.from_user.id, call.from_user.username)
    shop = await get_shop(callback_data.shop_id)
    if not shop:
        await call.answer(await t("catalog.shop_not_found"), show_alert=True)
        return
    tracked = await is_subscribed(user_id, shop.id)
    has_prices = bool(shop.price_start and shop.price_step is not None)
    kb = shop_card_kb(
        shop.id, tracked, src=callback_data.src,
        page=callback_data.page, flt=callback_data.flt, sort=callback_data.sort,
        has_prices=has_prices,
        maps_url=yandex_maps_url(shop.address),
    )
    # Список «превращается» в карточку на том же сообщении (delete+send
    # для text->photo — Telegram не умеет морфить это на месте).
    card_id = await show_shop_card(call, shop, date.today(), is_tracked=tracked, kb=kb)
    ws.open_card(user_id, card_id)
    await call.answer()


@router.callback_query(CatalogCb.filter(F.action == "sched"))
async def cb_schedule(call: CallbackQuery, callback_data: CatalogCb) -> None:
    user_id = await upsert_user(call.from_user.id, call.from_user.username)
    shop = await get_shop(callback_data.shop_id)
    if not shop:
        await call.answer(await t("catalog.shop_not_found"), show_alert=True)
        return
    tracked = await is_subscribed(user_id, shop.id)
    has_prices = bool(shop.price_start and shop.price_step is not None)
    new_id = await show_text_view(
        call,
        format_price_schedule(shop, date.today()),
        shop_card_kb(
            shop.id, tracked, src=callback_data.src,
            page=callback_data.page, flt=callback_data.flt, sort=callback_data.sort,
            has_prices=has_prices,
            maps_url=yandex_maps_url(shop.address),
            on_schedule=True,
        ),
    )
    ws.open_card(user_id, new_id)
    await call.answer()


# ---------- "More" sub-screen ----------


@router.callback_query(CatalogCb.filter(F.action == "more"))
async def cb_more(call: CallbackQuery, callback_data: CatalogCb) -> None:
    new_id = await render(
        call.bot, call.message.chat.id, call.message.message_id,
        await t("catalog.more_title"),
        more_filters_kb(callback_data.flt, callback_data.sort),
    )
    ws.set_active(call.from_user.id, new_id)
    await call.answer()


# ---------- Sort sub-screen ----------


@router.callback_query(CatalogCb.filter(F.action == "sort_open"))
async def cb_sort_open(call: CallbackQuery, callback_data: CatalogCb) -> None:
    new_id = await render(
        call.bot, call.message.chat.id, call.message.message_id,
        await t("catalog.sort_title"),
        sort_kb(callback_data.flt, callback_data.sort),
    )
    ws.set_active(call.from_user.id, new_id)
    await call.answer()


@router.callback_query(CatalogCb.filter(F.action == "sort_pick"))
async def cb_sort_pick(call: CallbackQuery, callback_data: CatalogCb, state: FSMContext) -> None:
    new_sort = _norm_sort(callback_data.value or SORT_NAME)
    if new_sort == SORT_NEARBY:
        # Активация сортировки «По расстоянию» — всегда переспрашиваем локацию,
        # чтобы каталог строился от текущего положения, а не от вчерашнего.
        await _ask_location(
            call, flt=_norm_flt(callback_data.flt), sort=SORT_NEARBY, state=state,
        )
        return
    await _render_catalog(call, page=0, flt=callback_data.flt, sort=new_sort, state=state)


# ---------- Search ----------


@router.callback_query(CatalogCb.filter(F.action == "search_start"))
async def cb_search_start(
    call: CallbackQuery, callback_data: CatalogCb, state: FSMContext,
) -> None:
    await state.set_state(CatalogStates.searching)
    new_id = await render(
        call.bot, call.message.chat.id, call.message.message_id,
        await t("catalog.search.prompt"),
        search_cancel_kb(callback_data.flt, callback_data.sort),
    )
    ws.set_active(call.from_user.id, new_id)
    await state.update_data(
        cat_flt=callback_data.flt,
        cat_sort=callback_data.sort,
        search_msg_id=new_id,
    )
    await call.answer()


@router.message(CatalogStates.searching, Command("cancel"))
async def msg_search_cancel(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    flt = _norm_flt(data.get("cat_flt", FLT_ALL))
    sort = _norm_sort(data.get("cat_sort", SORT_NAME))
    await state.clear()
    _user_search.pop(message.from_user.id, None)
    # Ввод пользователя удаляется, экран поиска снова становится каталогом.
    await _render_search_result(
        message, flt=flt, sort=sort, prompt_msg_id=data.get("search_msg_id"),
        state=state,
    )


@router.message(CatalogStates.searching, F.text)
async def msg_search_query(message: Message, state: FSMContext) -> None:
    query = (message.text or "").strip()[:_MAX_SEARCH_LEN]
    data = await state.get_data()
    flt = _norm_flt(data.get("cat_flt", FLT_ALL))
    sort = _norm_sort(data.get("cat_sort", SORT_NAME))
    await state.clear()
    if not query:
        _user_search.pop(message.from_user.id, None)
    else:
        _user_search[message.from_user.id] = query
    # Ввод пользователя удаляется, экран поиска снова становится каталогом
    # уже с применённым поиском.
    await _render_search_result(
        message, flt=flt, sort=sort, prompt_msg_id=data.get("search_msg_id"),
        state=state,
    )


async def _render_search_result(
    message: Message, *, flt: str, sort: str, prompt_msg_id: int | None,
    state: FSMContext | None = None,
) -> None:
    """Удалить ввод пользователя и на месте промпта нарисовать каталог."""
    try:
        await message.delete()
    except TelegramBadRequest:
        pass
    journal.remove(message.chat.id, message.message_id)
    if sort == SORT_NEARBY:
        point = _point_for(message.from_user.id)
    else:
        point = None
    user_id = await upsert_user(message.from_user.id, message.from_user.username)
    body, kb = await _catalog_payload(
        user_id, tg_id=message.from_user.id, page=0, flt=flt, sort=sort,
        point=point,
    )
    if prompt_msg_id is not None:
        new_id = await render(message.bot, message.chat.id, prompt_msg_id, body, kb)
    else:
        sent = await message.answer(body, reply_markup=kb, disable_web_page_preview=True)
        new_id = sent.message_id
    ws.set_active(user_id, new_id)


@router.callback_query(CatalogCb.filter(F.action == "search_clear"))
async def cb_search_clear(call: CallbackQuery, callback_data: CatalogCb, state: FSMContext) -> None:
    _user_search.pop(call.from_user.id, None)
    await _render_catalog(
        call, page=0, flt=callback_data.flt, sort=callback_data.sort, state=state,
    )


@router.callback_query(CatalogCb.filter(F.action == "search_cancel"))
async def cb_search_cancel(
    call: CallbackQuery, callback_data: CatalogCb, state: FSMContext,
) -> None:
    """«✖️ Отмена» с промпта поиска: гасим состояние и возвращаем каталог."""
    await state.clear()
    _user_search.pop(call.from_user.id, None)
    await _render_catalog(
        call, page=0, flt=callback_data.flt, sort=callback_data.sort, state=state,
    )


# ---------- Distance filter: location flow ----------


@router.message(CatalogStates.locating, F.location)
async def msg_catalog_location(message: Message, state: FSMContext) -> None:
    """Пользователь нажал «Поделиться местоположением» — рисуем каталог."""
    data = await state.get_data()
    await state.clear()
    point = (message.location.latitude, message.location.longitude)
    _user_location[message.from_user.id] = point
    flt = _norm_flt(data.get("cat_flt", FLT_ALL))
    sort = _norm_sort(data.get("cat_sort", SORT_NAME))
    user_id = await upsert_user(message.from_user.id, message.from_user.username)
    body, kb = await _catalog_payload(
        user_id, tg_id=message.from_user.id, page=0, flt=flt, sort=sort,
        point=point,
    )
    sent = await message.answer(body, reply_markup=kb, disable_web_page_preview=True)
    ws.set_active(user_id, sent.message_id)
    # Если локация пришла не через кнопку (например, пересылкой) — reply-клавиатура
    # могла остаться на экране. Сносим её коротким сообщением.
    try:
        removal = await message.answer("🗑", reply_markup=ReplyKeyboardRemove())
        await removal.delete()
    except TelegramBadRequest:
        pass
    await _delete_messages(
        message.bot, message.chat.id,
        [message.message_id, data.get("loc_msg_id")],
    )


async def _cancel_locating(message: Message, state: FSMContext) -> None:
    """Отмена запроса локации: гасим reply-клавиатуру и возвращаем каталог."""
    data = await state.get_data()
    await state.clear()
    flt = _norm_flt(data.get("cat_flt", FLT_ALL))
    sort = _norm_sort(data.get("cat_sort", SORT_NAME))
    # Точки пользователь не дал — сортировку «По расстоянию» нечего показывать
    # (без точки все магазины попадут в конец "без координат"), откатываемся
    # на «По названию».
    if sort == SORT_NEARBY and _point_for(message.from_user.id) is None:
        sort = SORT_NAME
    try:
        removal = await message.answer("🗑", reply_markup=ReplyKeyboardRemove())
        await removal.delete()
    except TelegramBadRequest:
        pass
    await _render_search_result(
        message, flt=flt, sort=sort, prompt_msg_id=data.get("loc_msg_id"),
    )


@router.message(CatalogStates.locating, Command("cancel"))
async def msg_locating_cancel(message: Message, state: FSMContext) -> None:
    await _cancel_locating(message, state)


@router.message(CatalogStates.locating, F.text == "👈 Назад")
async def msg_locating_back(message: Message, state: FSMContext) -> None:
    await _cancel_locating(message, state)


# ---------- Reset ----------


@router.callback_query(CatalogCb.filter(F.action == "reset"))
async def cb_reset(call: CallbackQuery, callback_data: CatalogCb) -> None:
    _user_search.pop(call.from_user.id, None)
    await _render_catalog(call, page=0, flt=FLT_ALL, sort=SORT_NAME)


# ---------- Misc ----------


@router.callback_query(F.data == "noop")
async def cb_noop(call: CallbackQuery) -> None:
    await call.answer()
