"""Batch-update shops' maps_url from a fresh list and refresh coordinates."""
import asyncio
import sys

sys.path.insert(0, "/app")

from data.db import close_db, init_db
from data.repos.shops import clear_shop_coords, get_shop, set_shop_coords, update_shop_field
from services.maps import resolve_shop_coords

UPDATES = {
    19: "https://yandex.ru/maps/-/CTtdeRLN",  # Favorite (Лебедя)
    5:  "https://yandex.ru/maps/-/CTtde8Yy",  # Favorite (ПОР)
    6:  "https://yandex.ru/maps/-/CTtdi08V",  # Favorite mini (ПОР)
    4:  "https://yandex.ru/maps/-/CTtdmCjQ",  # Евростиль (Московский рынок)
    3:  "https://yandex.ru/maps/-/CTtd4HKE",  # Megahand (Океан)
    14: "https://yandex.ru/maps/-/CTtdqZMe",  # Second Hand №1 (Комсомолка)
    13: "https://yandex.ru/maps/-/CTtdqXYh",  # Добрый Покупатель (Восставших)
    15: "https://yandex.ru/maps/-/CTtduQ1x",  # Евро (Центральный рынок)
    17: "https://yandex.ru/maps/-/CTtduG9K",  # Евро Стиль (Восставших)
    18: "https://yandex.ru/maps/-/CTtduDpH",  # Еврохенд (Пожарова)
    7:  "https://yandex.ru/maps/-/CTtdu2-m",  # Нетипичный Б/Утик (ПОР)
    11: "https://yandex.ru/maps/-/CTtdyF9h",  # Сток (Проспект Победы)
    1:  "https://yandex.ru/maps/-/CTtdyCNN",  # Твоя вещь (ПОР)
}


async def main() -> None:
    await init_db()
    ok = fail = 0
    for shop_id, url in UPDATES.items():
        shop = await get_shop(shop_id)
        if not shop:
            print(f"SKIP #{shop_id}: not found")
            continue
        await update_shop_field(shop_id, "maps_url", url)
        coords = await resolve_shop_coords(url)
        if coords:
            await set_shop_coords(shop_id, *coords)
            ok += 1
            print(f"OK   #{shop_id} {shop.name}: {coords[0]:.6f},{coords[1]:.6f}")
        else:
            await clear_shop_coords(shop_id)
            fail += 1
            print(f"FAIL #{shop_id} {shop.name}: {url}")
    print(f"\nok={ok} fail={fail}")
    await close_db()


if __name__ == "__main__":
    asyncio.run(main())