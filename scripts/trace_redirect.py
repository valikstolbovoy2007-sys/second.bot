"""Print redirect chain + first coords for a Yandex Maps URL. Usage: trace_redirect.py <url>."""
import asyncio
import sys
from urllib.parse import parse_qsl, urljoin

sys.path.insert(0, "/app")

from services.maps import _USER_AGENT, _make_session, extract_coords_from_url


async def main() -> None:
    url = sys.argv[1]
    session = _make_session()
    seen: set[str] = set()
    current = url
    try:
        for i in range(8):
            if current in seen:
                break
            seen.add(current)
            coords = extract_coords_from_url(current)
            print(f"[{i}] {current}  coords={coords}")
            async with session.get(
                current, allow_redirects=False,
                timeout=10.0, headers={"User-Agent": _USER_AGENT},
            ) as resp:
                print(f"    status={resp.status} url={resp.url}")
                location = resp.headers.get("Location")
                print(f"    location={location}")
                if not location:
                    break
                current = urljoin(current, location)
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc!r}")
    finally:
        await session.close()


if __name__ == "__main__":
    asyncio.run(main())