"""Билдеры URL и HTML-якоря для адресов магазинов в Яндекс.Картах.

Чистая строковая логика — без сети, без внешних API. Подходит и для рендера
карточки магазина, и для inline-кнопки «📍 В Яндекс.Картах».

Два формата URL, выбираются автоматически по входным данным:

    координаты ──► https://yandex.ru/maps/?ll=lon,lat&z=17&pt=lon,lat
                   точечная метка, не зависит от качества адресной строки

    адрес      ──► https://yandex.ru/maps/?text=Город,+адрес
                   текстовый поиск; префикс города не даёт Яндексу
                   уехать на «ул. Победы» в другом регионе

Безопасность для HTML parse_mode обеспечивает `yandex_maps_link_html`:
  • URL → `quote_plus` (URL-encoding query)
  • href → `html.escape(quote=True)` (экранирует `&` в `&amp;`)
  • текст ссылки → `html.escape` (экранирует `<`, `>`, `"`, `&`)

Кроме билдеров здесь же живёт обратная задача — достать координаты из
сохранённой пользователем ссылки Яндекс.Карт (нужно для distance-фильтра):
`extract_coords_from_url` снимает координаты с итогового URL после редиректов
(короткие ссылки `yandex.ru/maps/-/HASH`), `resolve_shop_coords` гоняет
редиректы и возвращает первую найденную метку.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
from urllib.parse import parse_qsl, quote_plus, urljoin

import aiohttp

from config import settings

log = logging.getLogger(__name__)

# Бот пока обслуживает только Севастополь. Параметр `city=` остаётся
# kwarg-ом — если когда-нибудь будем поддерживать другие города,
# достаточно прокинуть его из вызывающего кода (не трогая default).
DEFAULT_CITY = "Севастополь"

_BASE_URL = "https://yandex.ru/maps/"
# Городской зум: дом виден, но без подъездов.
_ZOOM = 17
# 6 знаков после запятой ≈ 10 см на местности — с запасом для метки магазина.
_COORD_PRECISION = 6


def yandex_maps_url(
    address: str,
    *,
    city: str = DEFAULT_CITY,
    lat: float | None = None,
    lon: float | None = None,
) -> str:
    """Собрать URL Яндекс.Карт.

    Приоритет — координаты: если заданы оба `lat`/`lon`, то адрес
    игнорируется и URL содержит точную метку. Иначе — текстовый
    поиск по «{city}, {address}».

    Если ни координат, ни адреса нет — возвращается пустая строка
    (вызывающему коду легко проверить «есть ли вообще ссылка»).
    """
    if lat is not None and lon is not None:
        # Важно: Яндекс ждёт сначала ДОЛГОТУ, потом ШИРОТУ. Перепутать
        # местами — метка уедет на другой полушар.
        ll = f"{lon:.{_COORD_PRECISION}f},{lat:.{_COORD_PRECISION}f}"
        return f"{_BASE_URL}?ll={ll}&z={_ZOOM}&pt={ll}"

    if not address or not address.strip():
        return ""

    query = _with_city(address, city)
    return f"{_BASE_URL}?text={quote_plus(query)}"


def yandex_maps_link_html(
    address: str,
    *,
    city: str = DEFAULT_CITY,
    lat: float | None = None,
    lon: float | None = None,
) -> str:
    """Создаёт ссылку из названия и URL, находящихся в одной ячейке.

    Формат address:
        ПОР https://yandex.ru/maps/...
        Московский рынок https://yandex.ru/maps/...

    В ссылку превращается только текст до URL.
    """

    if not address or not address.strip():
        return ""

    # Если координаты переданы — сохраняем старое поведение.
    if lat is not None and lon is not None:
        url = yandex_maps_url(
            address,
            city=city,
            lat=lat,
            lon=lon,
        )
        href = html.escape(url, quote=True)
        text = html.escape(address.strip())
        return f'<a href="{href}">{text}</a>'

    value = address.strip()

    # Ищем URL внутри значения address.
    match = re.search(r"https?://\S+", value)

    if match:
        title = value[:match.start()].strip()
        url = match.group(0).strip()

        # Убираем возможные разделители перед URL:
        # "ПОР - https://..."
        # "ПОР | https://..."
        # "ПОР — https://..."
        title = re.sub(r"\s*[-|—:]+\s*$", "", title).strip()

        if title:
            return (
                f'<a href="{html.escape(url, quote=True)}">'
                f'{html.escape(title)}'
                f'</a>'
            )

    # Если URL в ячейке нет — старое поведение.
    url = yandex_maps_url(value, city=city)
    href = html.escape(url, quote=True)
    text = html.escape(value)

    return f'<a href="{href}">{text}</a>'


def _with_city(address: str, city: str) -> str:
    """Приклеить «{city}, » к адресу, если города в нём ещё нет.

    Сравнение case-insensitive substring — корректно ловит и «Севастополь, …»,
    и «…, г. Севастополь», и «севастополь …», не дублируя префикс.
    """
    a = address.strip()
    if city.lower() in a.lower():
        return a
    return f"{city}, {a}"


# ---------------------------------------------------------------------------
# Извлечение координат из ссылок Яндекс.Карт (для distance-фильтра)
# ---------------------------------------------------------------------------

# Приоритет параметров итогового URL: явная метка (pt) → центр поиска (sll) →
# центр вьюпорта (ll) → последний центр (cl). Ниже по приоритету — грубее.
_COORD_PARAMS = ("pt", "sll", "ll", "cl")


def _parse_coord(value: str) -> tuple[float, float] | None:
    """Парсит «lon,lat» → кортеж (lat, lon). Яндекс кладёт долготу первой."""
    try:
        lon_str, _, lat_str = value.strip().partition(",")
        lon = float(lon_str)
        lat = float(lat_str)
    except (TypeError, ValueError):
        return None
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        return None
    return lat, lon


def extract_coords_from_url(url: str) -> tuple[float, float] | None:
    """Снять координаты с query-строки URL Яндекс.Карт.

    Понимает обычные (`ll`/`sll`/`pt`) и пошеринг-ссылку с меткой (`pt`).
    Возвращает (lat, lon) или None.

    Ссылки-маршруты (`mode=routes`, `rtext=...` — это «как проехать», а не
    точка магазина) сознательно пропускаются: их `ll` — центр карты, а не
    объект.
    """
    try:
        query = url.split("?", 1)[1]
    except IndexError:
        return None
    params = dict(parse_qsl(query))
    if "rtext" in params or params.get("mode") == "routes":
        return None
    for key in _COORD_PARAMS:
        raw = params.get(key)
        if not raw:
            continue
        # pt может содержать несколько меток через «~»; берём первую.
        coords = _parse_coord(raw.split("~", 1)[0])
        if coords is not None:
            return coords
    return None


_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _make_session() -> aiohttp.ClientSession:
    """Сессия для запросов к Яндекс.Картам; учитывает PROXY_URL из конфига."""
    kwargs: dict = {"trust_env": True}
    if settings.PROXY_URL:
        kwargs["proxy"] = settings.PROXY_URL
    return aiohttp.ClientSession(**kwargs)


async def _first_coords_after_redirects(
    url: str, session: aiohttp.ClientSession, *, timeout: float,
) -> tuple[float, float] | None:
    """Идти по редиректам (до 6 шагов), на итоговых URL искать координаты.

    Координаты проверяются на КАЖДОМ шаге, а не только в конце: короткие
    ссылки ведут на share-страницу с координатами раньше, чем на финальный орг.
    """
    seen: set[str] = set()
    current = url
    total = aiohttp.ClientTimeout(total=timeout)
    for _ in range(6):
        if current in seen:
            return None
        seen.add(current)
        direct = extract_coords_from_url(current)
        if direct is not None:
            return direct
        try:
            async with session.get(
                current,
                allow_redirects=False,
                timeout=total,
                headers={"User-Agent": _USER_AGENT},
            ) as resp:
                location = resp.headers.get("Location")
                if not location:
                    return None
                current = urljoin(current, location)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            log.warning("maps: fetch %s failed", current, exc_info=True)
            return None
    return extract_coords_from_url(current)


async def resolve_shop_coords(maps_url: str | None) -> tuple[float, float] | None:
    """Достать координаты магазина из сохранённой ссылки Яндекс.Карт.

    Умеет:
      • URL-метки вида `?ll=lon,lat` / `&pt=lon,lat` (наш собственный формат);
      • короткие ссылки `yandex.ru/maps/-/HASH` — идёт по редиректам;
      • org-ссылки `yandex.ru/maps/org/...` — не редиректят на координатные
        URL, вернёт None (за парсинг тела страницы не берёмся).

    None — координаты неизвестны (магазин не участвует в distance-фильтре).
    """
    if not maps_url or not maps_url.strip():
        return None
    session = _make_session()
    try:
        return await _first_coords_after_redirects(maps_url.strip(), session, timeout=10.0)
    finally:
        await session.close()
