"""Resolve a single Yandex Maps URL to (lat, lon). Usage: resolve_one.py <url>."""
import asyncio
import sys

sys.path.insert(0, "/app")

from services.maps import resolve_shop_coords


async def main() -> None:
    coords = await resolve_shop_coords(sys.argv[1])
    print(f"coords={coords}")

asyncio.run(main())