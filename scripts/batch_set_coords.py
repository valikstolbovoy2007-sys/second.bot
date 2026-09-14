"""Set exact pin coordinates for shops (user-provided). Also updates maps_url.

Run in container: PYTHONPATH=/app python3 /app/scripts/batch_set_coords.py
"""
import asyncio
import sys

sys.path.insert(0, "/app")

from data.db import close_db, init_db
from data.repos.shops import get_shop, set_shop_coords, update_shop_field

COORDS = {
    # id: (url, lat, lng)
    19: ("https://yandex.ru/maps/-/CTtd646z", 44.561623, 33.525821),  # Favorite (Лебедя)
    5:  ("https://yandex.ru/maps/-/CTtde8Yy", 44.588247, 33.460973),  # Favorite (ПОР)
    6:  ("https://yandex.ru/maps/-/CTtdi08V", 44.588756, 33.460167),  # Favorite mini (ПОР)
    4:  ("https://yandex.ru/maps/-/CTtdmCjQ", 44.581015, 33.518342),  # Евростиль (Московский рынок)
    3:  ("https://yandex.ru/maps/-/CTtd4HKE", 44.575394, 33.521094),  # Megahand (Океан)
    14: ("https://yandex.ru/maps/-/CTtdqZMe", 44.610086, 33.518219),  # Second Hand №1 (Комсомолка)
    13: ("https://yandex.ru/maps/-/CTtdqXYh", 44.600487, 33.514941),  # Добрый Покупатель (Восставших)
    15: ("https://yandex.ru/maps/-/CTtduQ1x", 44.610849, 33.518109),  # Евро (Центральный рынок)
    17: ("https://yandex.ru/maps/-/CTtduG9K", 44.602268, 33.514796),  # Евро Стиль (Восставших)
    18: ("https://yandex.ru/maps/-/CTtduDpH", 44.605232, 33.508192),  # Еврохенд (Пожарова)
    7:  ("https://yandex.ru/maps/-/CTtdu2-m", 44.591601, 33.457627),  # Нетипичный Б/Утик (ПОР)
    11: ("https://yandex.ru/maps/-/CTtdyF9h", 44.587191, 33.559560),  # Сток (Проспект Победы)
    1:  ("https://yandex.ru/maps/-/CTtdyCNN", 44.589482, 33.462370),  # Твоя вещь (ПОР)
}


async def main() -> None:
    await init_db()
    for shop_id, (url, lat, lng) in COORDS.items():
        shop = await get_shop(shop_id)
        if not shop:
            print(f"SKIP #{shop_id}: not found")
            continue
        await update_shop_field(shop_id, "maps_url", url)
        await set_shop_coords(shop_id, lat, lng)
        print(f"OK #{shop_id} {shop.name}: {lat:.6f},{lng:.6f}")
    await close_db()


if __name__ == "__main__":
    asyncio.run(main())