"""Block-based shop card template.

The card is composed of an ordered list of named blocks. Each block is a
piece of DSL template (same syntax as `card_template.py`: `{var}` and
`{?var:body}`). Super-admin manages the list via toggle / move / edit
per-block — without seeing the whole template as one string.

Storage: bot_config[shop_card_blocks] — JSON array of
    {"id": str, "enabled": bool, "custom_text"?: str}

The fixed registry of blocks (DEFAULT_BLOCKS) is declared in code.
Concatenating DEFAULT_BLOCKS' default_text in order reproduces the
legacy DEFAULT_TEMPLATE byte-for-byte (golden-tested).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)

CONFIG_KEY_BLOCKS = "shop_card_blocks"

_MAX_CUSTOM_LEN = 500


@dataclass(frozen=True)
class BlockSpec:
    id: str
    title: str                  # UI title («🚚 Блок завоза»)
    default_text: str           # DSL fragment used when not customized
    editable: bool              # SA can edit text (false for separators)
    variables: tuple[str, ...]  # vars used by this block (hint for SA UI)


# Order matters — concatenation reproduces the legacy DEFAULT_TEMPLATE.
DEFAULT_BLOCKS: tuple[BlockSpec, ...] = (
    BlockSpec(
        id="header",
        title="🏪 Заголовок",
        default_text="🏪 <b>{name}</b>\n{?chain:<i>{chain}</i>\n}\n",
        editable=True,
        variables=("name", "chain"),
    ),
    BlockSpec(
        id="price_today",
        title="💰 Цена сегодня",
        default_text="{?price_today_line:{price_today_line}\n{?day_label:{day_label}\n}}\n",
        editable=True,
        variables=("price_today_line", "day_label"),
    ),
    BlockSpec(
        id="arrival_block",
        title="🚚 Блок завоза",
        default_text=(
            "<blockquote>🚚 Следующий завоз: <b>{next_arrival_date}</b> "
            "({days_to_arrival})\n"
            "{?price_start:В день завоза: <b>{price_start} ₽/кг</b>}</blockquote>\n"
        ),
        editable=True,
        variables=("next_arrival_date", "days_to_arrival", "price_start"),
    ),
    BlockSpec(
        id="today_event",
        title="⚡ Событие сегодня",
        default_text="{?today_event:\n⚡ Сегодня: {today_event}\n}\n",
        editable=True,
        variables=("today_event",),
    ),
    BlockSpec(
        id="address",
        title="📍 Адрес",
        default_text="📍 {address_link}\n",
        editable=True,
        variables=("address_link",),
    ),
    BlockSpec(
        id="working_hours",
        title="🕒 Время работы",
        # Ведущий \n внутри условного блока, как и у соседних блоков
        # (description) — чтобы при отключении не оставались «двойные» пустые
        # строки и assemble() побайтово совпадал с DEFAULT_TEMPLATE.
        default_text="{?working_hours:\n🕒 {working_hours}\n}",
        editable=True,
        variables=("working_hours",),
    ),
    BlockSpec(
        id="description",
        title="📝 Описание",
        default_text="{?description:\n{description}\n}",
        editable=True,
        variables=("description",),
    ),
    BlockSpec(
        id="tracked_badge",
        title="⭐ Метка отслеживания",
        default_text="{?is_tracked:\n⭐ <i>Ты отслеживаешь этот магазин</i>}",
        editable=True,
        variables=("is_tracked",),
    ),
)

_SPECS_BY_ID: dict[str, BlockSpec] = {b.id: b for b in DEFAULT_BLOCKS}


def get_spec(block_id: str) -> BlockSpec | None:
    return _SPECS_BY_ID.get(block_id)


def all_specs() -> tuple[BlockSpec, ...]:
    return DEFAULT_BLOCKS


# ---------- stored configuration ----------


@dataclass
class BlockConfig:
    id: str
    enabled: bool = True
    custom_text: str | None = None


def default_blocks_config() -> list[BlockConfig]:
    return [BlockConfig(id=b.id, enabled=True, custom_text=None) for b in DEFAULT_BLOCKS]


def to_json(blocks: list[BlockConfig]) -> str:
    out = []
    for b in blocks:
        item: dict = {"id": b.id, "enabled": b.enabled}
        if b.custom_text is not None:
            item["custom_text"] = b.custom_text
        out.append(item)
    return json.dumps(out, ensure_ascii=False)


def from_json(raw: str | None) -> list[BlockConfig]:
    """Tolerant parser. On any failure or unknown blocks → defaults.

    Unknown block ids are skipped. Missing blocks (e.g., new ones added in
    code after the JSON was saved) are appended at the end with defaults.
    """
    if not raw:
        return default_blocks_config()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        log.warning("shop_card_blocks: invalid JSON, falling back to defaults")
        return default_blocks_config()
    if not isinstance(data, list):
        log.warning("shop_card_blocks: not a list, falling back to defaults")
        return default_blocks_config()
    parsed: list[BlockConfig] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        bid = item.get("id")
        if not isinstance(bid, str) or bid not in _SPECS_BY_ID or bid in seen:
            continue
        seen.add(bid)
        ct = item.get("custom_text")
        if not isinstance(ct, str):
            ct = None
        parsed.append(BlockConfig(
            id=bid,
            enabled=bool(item.get("enabled", True)),
            custom_text=ct,
        ))
    for spec in DEFAULT_BLOCKS:
        if spec.id not in seen:
            parsed.append(BlockConfig(id=spec.id, enabled=True, custom_text=None))
    return parsed


# ---------- assembly ----------


def assemble(blocks: list[BlockConfig]) -> str:
    """Concatenate enabled blocks' texts into one DSL template string."""
    parts: list[str] = []
    for cfg in blocks:
        spec = _SPECS_BY_ID.get(cfg.id)
        if spec is None or not cfg.enabled:
            continue
        text = cfg.custom_text if cfg.custom_text is not None else spec.default_text
        parts.append(text)
    return "".join(parts)


# ---------- user-facing view of a block's text ----------

_COND_OPEN_RE = re.compile(r"\{\?(!?)([a-z_]+):")


def simplify_for_user(text: str) -> str:
    """Strip DSL conditional wrappers so SA edits plain prose.

      `{?var:body}`  -> `body`
      `{?!var:body}` -> ``  (its purpose was «show when empty» — useless
                              once SA is rewriting the block)
    """
    out = text
    # Bounded loop in case of malformed input.
    for _ in range(64):
        m = _COND_OPEN_RE.search(out)
        if not m:
            break
        depth = 1
        i = m.end()
        body_start = i
        while i < len(out) and depth > 0:
            ch = out[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            i += 1
        if depth != 0:
            break  # malformed; leave alone
        body = out[body_start:i - 1]
        replacement = "" if m.group(1) == "!" else body
        out = out[: m.start()] + replacement + out[i:]
    return out


# ---------- validation of user-submitted block text ----------

_FORBIDDEN_COND_RE = re.compile(r"\{\?")


def validate_custom_text(text: str) -> tuple[bool, str]:
    if len(text) > _MAX_CUSTOM_LEN:
        return False, f"Текст блока слишком длинный (>{_MAX_CUSTOM_LEN} символов)."
    if _FORBIDDEN_COND_RE.search(text):
        return False, (
            "Условные блоки <code>{?…}</code> нельзя редактировать вручную. "
            "Используй обычные подстановки вида <code>{name}</code>, или "
            "сбрось блок к стандартному."
        )
    return True, ""


# ---------- mutation helpers ----------


def find_index(blocks: list[BlockConfig], block_id: str) -> int:
    for i, b in enumerate(blocks):
        if b.id == block_id:
            return i
    return -1


def move(blocks: list[BlockConfig], block_id: str, direction: int) -> bool:
    """Move block up (direction=-1) or down (direction=+1). Returns True if moved."""
    i = find_index(blocks, block_id)
    if i < 0:
        return False
    j = i + direction
    if not 0 <= j < len(blocks):
        return False
    blocks[i], blocks[j] = blocks[j], blocks[i]
    return True


def toggle(blocks: list[BlockConfig], block_id: str) -> bool:
    """Flip `enabled`. Returns the new value, or False if block not found."""
    i = find_index(blocks, block_id)
    if i < 0:
        return False
    blocks[i].enabled = not blocks[i].enabled
    return blocks[i].enabled


def set_custom_text(blocks: list[BlockConfig], block_id: str, text: str) -> bool:
    i = find_index(blocks, block_id)
    if i < 0:
        return False
    blocks[i].custom_text = text
    return True


def reset_block(blocks: list[BlockConfig], block_id: str) -> bool:
    """Drop custom_text, re-enable. Returns True if block found."""
    i = find_index(blocks, block_id)
    if i < 0:
        return False
    blocks[i].custom_text = None
    blocks[i].enabled = True
    return True
