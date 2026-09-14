"""Update a shop's maps_url (and refresh its coordinates). Usage: update_shop_url.py <id> <url>."""
import asyncio
import sys

sys.path.insert(0, "/app")

from data.db import close_db, init_db
from data.repos.shops import clear_shop_coords, get_shop, set_shop_coords, update_shop_field
from services.maps import resolve_shop_coords


async def main() -> None:
    shop_id = int(sys.argv[1])
    url = sys.argv[2]
    await init_db()
    shop = await get_shop(shop_id)
    if not shop:
        print(f"shop #{shop_id} not found")
        return
    await update_shop_field(shop_id, "maps_url", url)
    coords = await resolve_shop_coords(url)
    if coords:
        await set_shop_coords(shop_id, *coords)
        print(f"OK #{shop_id} {shop.name}: {coords[0]:.6f},{coords[1]:.6f}")
    else:
        await clear_shop_coords(shop_id)
        print(f"FAIL #{shop_id} {shop.name}: no coords from {url}")
    await close_db()


if __name__ == "__main__":
    asyncio.run(main())