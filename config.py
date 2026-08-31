import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _parse_ids(raw: str) -> list[int]:
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    BOT_TOKEN: str
    DATABASE_URL: str
    ADMIN_IDS: list[int]
    SUPER_ADMIN_IDS: list[int]
    ADMIN_CHAT_ID: int | None
    LOG_LEVEL: str
    PROXY_URL: str | None


settings = Settings(
    BOT_TOKEN=os.environ["BOT_TOKEN"],
    DATABASE_URL=os.environ["DATABASE_URL"],
    ADMIN_IDS=_parse_ids(os.getenv("ADMIN_IDS", "")),
    SUPER_ADMIN_IDS=_parse_ids(os.getenv("SUPER_ADMIN_IDS", "") or os.getenv("ADMIN_IDS", "")),
    ADMIN_CHAT_ID=int(os.environ["ADMIN_CHAT_ID"]) if os.getenv("ADMIN_CHAT_ID") else None,
    LOG_LEVEL=os.getenv("LOG_LEVEL", "INFO"),
    PROXY_URL=os.getenv("PROXY_URL") or None,
)
