"""Print shops id|name|maps_url|lat|lng to stdout (for debugging)."""
import asyncio
import sys
sys.path.insert(0, "/app")

from data.db import init_db, close_db
from data.repos.shops import list_active_shops


async def main():
    await init_db()
    shops = await list_active_shops()
    for s in shops:
        url = (s.maps_url or "")[:100]
        print(f"{s.id}|{s.name}|{url}|{s.lat}|{s.lng}")
    await close_db()

asyncio.run(main())
