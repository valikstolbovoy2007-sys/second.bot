"""Print shops id|name|maps_url|lat|lng to stdout (for debugging)."""
import asyncio
import sys
sys.path.insert(0, "/app")

from data.repos.shops import list_active_shops


async def main():
    shops = await list_active_shops()
    for s in shops:
        print(f"{s.id}|{s.name}|{s.maps_url or ''}|{s.lat}|{s.lng}")

asyncio.run(main())
