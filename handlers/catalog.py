import html
import logging
from datetime import date

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

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
from services.maps import yandex_maps_url
from services.catalog import (
    FLT_ALL,
    SORT_NAME,
    VALID_FILTERS,
    VALID_SORTS,
    apply,
)
from services.texts import t
from states.catalog_states import CatalogStates

log = logging.getLogger(__name__)
router = Router(name="catalog")

# In-memory map: telegram user_id → active search query.
# Ephemeral by design — search is cleared on bot restart, which is fine.
_user_search: dict[int, str] = {}
_MAX_SEARCH_LEN = 60


def _phase_markers(shops: list[Shop], today: date) -> dict[int, str]:
    return {s.id: m for s in shops if (m := phase_marker(s, today))}


def _norm_flt(flt: str) -> str:
    return flt if flt in VALID_FILTERS else FLT_ALL


def _norm_sort(sort: str) -> str:
    return sort if sort in VALID_SORTS else SORT_NAME


async def _build_header(
    *, total: int, flt: str, sort: str, search: str,
) -> str:
    parts = [await t("catalog.title"), await t("catalog.found", count=total)]
    chips: list[str] = []
    if flt in FILTER_SHORT:
        chips.append(FILTER_SHORT[flt])
    if sort != SORT_NAME:
        chips.append(f"↕ {SORT_LABELS[sort]}")
    if search:
        chips.append(f"🔍 «{html.escape(search)}»")
    if chips:
        parts.append(" · ".join(chips))
    parts.append("")
    parts.append(await t("catalog.legend"))
    parts.append("")
    parts.append(await t("catalog.hint"))
    return "\n".join(parts)


async def _render_catalog(
    call: CallbackQuery,
    *,
    page: int,
    flt: str,
    sort: str,
) -> None:
    user_id = await upsert_user(call.from_user.id, call.from_user.username)
    flt = _norm_flt(flt)
    sort = _norm_sort(sort)
    search = _user_search.get(call.from_user.id, "")
    today = date.today()

    all_shops = await list_active_shops()
    sub_ids = await subscribed_shop_ids(user_id)
    filtered = apply(
        all_shops, today,
        flt=flt, sort=sort, search=search,
        subscribed_ids=sub_ids,
    )

    total = len(filtered)
    if total == 0:
        body = await t(
            "catalog.empty_filter" if (flt != FLT_ALL or search) else "catalog.empty"
        )
        await show_text_view(call, body, empty_filter_kb(flt, has_search=bool(search)))
        await call.answer()
        return

    last_page = max(0, (total - 1) // PAGE_SIZE)
    page = max(0, min(page, last_page))
    page_shops = filtered[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    markers = _phase_markers(page_shops, today)

    body = await _build_header(total=total, flt=flt, sort=sort, search=search)
    kb = catalog_kb(
        page_shops, page, flt, sort, total,
        has_search=bool(search),
        phase_markers=markers,
        tracked_ids=sub_ids,
    )
    await show_text_view(call, body, kb)
    await call.answer()


# ---------- entry points ----------


@router.callback_query(F.data == "catalog:open")
async def cb_open(call: CallbackQuery) -> None:
    await _render_catalog(call, page=0, flt=FLT_ALL, sort=SORT_NAME)


@router.callback_query(CatalogCb.filter(F.action == "list"))
async def cb_list(call: CallbackQuery, callback_data: CatalogCb) -> None:
    await _render_catalog(
        call, page=callback_data.page, flt=callback_data.flt, sort=callback_data.sort,
    )


@router.callback_query(CatalogCb.filter(F.action == "filter"))
async def cb_filter(call: CallbackQuery, callback_data: CatalogCb) -> None:
    await _render_catalog(
        call, page=0, flt=callback_data.flt, sort=callback_data.sort,
    )


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
        shop.id, tracked, src="cat",
        page=callback_data.page, flt=callback_data.flt, sort=callback_data.sort,
        has_prices=has_prices,
        maps_url=yandex_maps_url(shop.address),
    )
    await show_shop_card(call, shop, date.today(), is_tracked=tracked, kb=kb)
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
    await show_text_view(
        call,
        format_price_schedule(shop, date.today()),
        shop_card_kb(
            shop.id, tracked, src="cat",
            page=callback_data.page, flt=callback_data.flt, sort=callback_data.sort,
            has_prices=has_prices,
            maps_url=yandex_maps_url(shop.address),
            on_schedule=True,
        ),
    )
    await call.answer()


# ---------- "More" sub-screen ----------


@router.callback_query(CatalogCb.filter(F.action == "more"))
async def cb_more(call: CallbackQuery, callback_data: CatalogCb) -> None:
    body = await t("catalog.more_title")
    try:
        await call.message.edit_text(
            body, reply_markup=more_filters_kb(callback_data.flt, callback_data.sort),
        )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise
    await call.answer()


# ---------- Sort sub-screen ----------


@router.callback_query(CatalogCb.filter(F.action == "sort_open"))
async def cb_sort_open(call: CallbackQuery, callback_data: CatalogCb) -> None:
    body = await t("catalog.sort_title")
    try:
        await call.message.edit_text(
            body, reply_markup=sort_kb(callback_data.flt, callback_data.sort),
        )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise
    await call.answer()


@router.callback_query(CatalogCb.filter(F.action == "sort_pick"))
async def cb_sort_pick(call: CallbackQuery, callback_data: CatalogCb) -> None:
    new_sort = _norm_sort(callback_data.value or SORT_NAME)
    await _render_catalog(call, page=0, flt=callback_data.flt, sort=new_sort)


# ---------- Search ----------


@router.callback_query(CatalogCb.filter(F.action == "search_start"))
async def cb_search_start(
    call: CallbackQuery, callback_data: CatalogCb, state: FSMContext,
) -> None:
    await state.set_state(CatalogStates.searching)
    await state.update_data(
        cat_flt=callback_data.flt, cat_sort=callback_data.sort,
    )
    try:
        await call.message.edit_text(
            await t("catalog.search.prompt"),
            reply_markup=search_cancel_kb(callback_data.flt, callback_data.sort),
        )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise
    await call.answer()


@router.message(CatalogStates.searching, Command("cancel"))
async def msg_search_cancel(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    flt = _norm_flt(data.get("cat_flt", FLT_ALL))
    sort = _norm_sort(data.get("cat_sort", SORT_NAME))
    await state.clear()
    _user_search.pop(message.from_user.id, None)
    # Re-open catalog as a fresh message (no callback context here).
    await _render_after_text(message, flt=flt, sort=sort)


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
    await _render_after_text(message, flt=flt, sort=sort)


async def _render_after_text(message: Message, *, flt: str, sort: str) -> None:
    """Render catalog after a text message (no callback to edit — send new)."""
    user_id = await upsert_user(message.from_user.id, message.from_user.username)
    today = date.today()
    search = _user_search.get(message.from_user.id, "")
    all_shops = await list_active_shops()
    sub_ids = await subscribed_shop_ids(user_id)
    filtered = apply(
        all_shops, today, flt=flt, sort=sort, search=search, subscribed_ids=sub_ids,
    )
    total = len(filtered)
    if total == 0:
        body = await t(
            "catalog.empty_filter" if (flt != FLT_ALL or search) else "catalog.empty"
        )
        await message.answer(body, reply_markup=empty_filter_kb(flt, has_search=bool(search)))
        return
    page = 0
    page_shops = filtered[: PAGE_SIZE]
    markers = _phase_markers(page_shops, today)
    body = await _build_header(total=total, flt=flt, sort=sort, search=search)
    kb = catalog_kb(
        page_shops, page, flt, sort, total,
        has_search=bool(search),
        phase_markers=markers,
        tracked_ids=sub_ids,
    )
    await message.answer(body, reply_markup=kb)


@router.callback_query(CatalogCb.filter(F.action == "search_clear"))
async def cb_search_clear(call: CallbackQuery, callback_data: CatalogCb) -> None:
    _user_search.pop(call.from_user.id, None)
    await _render_catalog(
        call, page=0, flt=callback_data.flt, sort=callback_data.sort,
    )


# ---------- Reset ----------


@router.callback_query(CatalogCb.filter(F.action == "reset"))
async def cb_reset(call: CallbackQuery, callback_data: CatalogCb) -> None:
    _user_search.pop(call.from_user.id, None)
    await _render_catalog(call, page=0, flt=FLT_ALL, sort=SORT_NAME)


# ---------- Misc ----------


@router.callback_query(F.data == "noop")
async def cb_noop(call: CallbackQuery) -> None:
    await call.answer()
