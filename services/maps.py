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
"""
from __future__ import annotations

import html
import re
from urllib.parse import quote_plus

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
