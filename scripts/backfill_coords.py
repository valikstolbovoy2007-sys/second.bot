"""Бэкфилл координат магазинов для фильтра «По расстоянию».

Берёт каждую сохранённую ссылку Яндекс.Карт (shop.maps_url), идёт по
редиректам и достаёт координаты (см. services.maps.resolve_shop_coords).
Магазины, у которых ссылки нет или координаты не извлекаются, остаются
без lat/lng — они не показываются в distance-фильтре.

Запуск в контейнере:
    docker compose exec -T bot sh -c 'PYTHONPATH=/app python3 /app/scripts/backfill_coords.py'
"""
import asyncio
import logging

from data.db import close_db, init_db
from data.repos.shops import clear_shop_coords, list_all_shops, set_shop_coords
from services.maps import resolve_shop_coords

log = logging.getLogger("backfill_coords")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


async def main() -> None:
    await init_db()
    shops = await list_all_shops()
    with_coords = without = no_url = 0
    for shop in shops:
        if not shop.maps_url:
            no_url += 1
            continue
        coords = await resolve_shop_coords(shop.maps_url)
        if coords:
            await set_shop_coords(shop.id, *coords)
            with_coords += 1
            print(f"OK   #{shop.id} {shop.name}: {coords[0]:.6f},{coords[1]:.6f}")
        else:
            await clear_shop_coords(shop.id)
            without += 1
            print(f"FAIL #{shop.id} {shop.name}: {shop.maps_url}")
    print(f"\nвсего={len(shops)} с координатами={with_coords} без={without} без ссылки={no_url}")
    await close_db()


if __name__ == "__main__":
    asyncio.run(main())