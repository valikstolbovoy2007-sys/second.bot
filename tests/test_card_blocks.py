"""Tests for the block-based card template (services/card_blocks.py)."""
from datetime import date, timedelta

#import pytest

from data.repos.shops import Shop
from services.card_blocks import (
    BlockConfig,
    DEFAULT_BLOCKS,
    assemble,
    default_blocks_config,
    from_json,
    get_spec,
    move,
    reset_block,
    set_custom_text,
    simplify_for_user,
    to_json,
    toggle,
    validate_custom_text,
)
from services.card_template import (
    DEFAULT_TEMPLATE,
    build_context,
    render,
)


def _sample_shop() -> Shop:
    return Shop(
        id=0,
        name="Тест",
        address="Тестовая, 1",
        description="Описание",
        chain_name="Megahand",
        cycle_length=14,
        anchor_date=date.today() - timedelta(days=3),
        price_start=1200,
        price_step=50,
        working_hours="Пн-Сб 10:00–21:00",
        is_active=True,
        maps_url=None,
    )


# ---------- golden: defaults reproduce legacy template ----------


def test_assemble_default_matches_legacy_template_string():
    assembled = assemble(default_blocks_config())
    assert assembled == DEFAULT_TEMPLATE


def test_assemble_default_renders_identically_to_legacy():
    shop = _sample_shop()
    ctx = build_context(shop, date.today(), is_tracked=True)
    legacy = render(DEFAULT_TEMPLATE, ctx)
    new = render(assemble(default_blocks_config()), ctx)
    assert legacy == new


# ---------- serialization ----------


def test_to_from_json_round_trip():
    blocks = default_blocks_config()
    blocks[0].enabled = False
    blocks[2].custom_text = "🏪 {name}"
    restored = from_json(to_json(blocks))
    assert len(restored) == len(blocks)
    for a, b in zip(blocks, restored):
        assert a.id == b.id
        assert a.enabled == b.enabled
        assert a.custom_text == b.custom_text


def test_from_json_empty_returns_default():
    cfg = from_json(None)
    assert cfg == default_blocks_config()
    assert from_json("") == default_blocks_config()


def test_from_json_garbage_returns_default():
    assert from_json("not json") == default_blocks_config()
    assert from_json('{"not": "a list"}') == default_blocks_config()


def test_from_json_unknown_block_skipped_and_missing_appended():
    raw = '[{"id": "unknown_block", "enabled": true}, {"id": "header", "enabled": false}]'
    cfg = from_json(raw)
    # All known blocks present (unknown is dropped, missing are appended).
    ids = [c.id for c in cfg]
    assert "unknown_block" not in ids
    assert set(ids) == {b.id for b in DEFAULT_BLOCKS}
    # Header keeps its `enabled=false` from JSON.
    assert next(c for c in cfg if c.id == "header").enabled is False


def test_from_json_duplicate_ids_keep_first():
    raw = '[{"id": "header", "enabled": false}, {"id": "header", "enabled": true}]'
    cfg = from_json(raw)
    headers = [c for c in cfg if c.id == "header"]
    assert len(headers) == 1
    assert headers[0].enabled is False


# ---------- assembly ----------


def test_assemble_skips_disabled_blocks():
    blocks = default_blocks_config()
    # disable header
    toggle(blocks, "header")
    assembled = assemble(blocks)
    # The header default text starts with "🏪 <b>{name}</b>". Once disabled,
    # the result must not contain it.
    assert "🏪 <b>{name}</b>" not in assembled
    # But it should still contain another known fragment.
    assert "{address_link}" in assembled


def test_assemble_uses_custom_text_when_set():
    blocks = default_blocks_config()
    set_custom_text(blocks, "address", "addr={address}")
    assembled = assemble(blocks)
    assert "addr={address}" in assembled
    # default address default text is gone
    assert "📍 {address_link}" not in assembled


# ---------- mutations ----------


def test_move_up_and_down():
    blocks = default_blocks_config()
    ids_before = [b.id for b in blocks]
    # move price_today up by 1
    assert move(blocks, "price_today", -1) is True
    ids_after = [b.id for b in blocks]
    i_before = ids_before.index("price_today")
    i_after = ids_after.index("price_today")
    assert i_after == i_before - 1


def test_move_off_edges_is_noop():
    blocks = default_blocks_config()
    first_id = blocks[0].id
    last_id = blocks[-1].id
    assert move(blocks, first_id, -1) is False
    assert move(blocks, last_id, +1) is False
    assert [b.id for b in blocks] == [b.id for b in default_blocks_config()]


def test_toggle_flips_enabled():
    blocks = default_blocks_config()
    assert toggle(blocks, "header") is False  # was True, now False
    assert toggle(blocks, "header") is True


def test_reset_block_restores_defaults():
    blocks = default_blocks_config()
    set_custom_text(blocks, "header", "custom")
    toggle(blocks, "header")  # now disabled
    reset_block(blocks, "header")
    h = next(b for b in blocks if b.id == "header")
    assert h.custom_text is None
    assert h.enabled is True


# ---------- validation of user input ----------


def test_validate_custom_text_rejects_conditional_syntax():
    ok, err = validate_custom_text("hello {?name:body}")
    assert ok is False
    assert "{?" in err or "Условные" in err


def test_validate_custom_text_rejects_too_long():
    ok, err = validate_custom_text("x" * 600)
    assert ok is False
    assert "длинный" in err


def test_validate_custom_text_accepts_plain_with_vars():
    ok, err = validate_custom_text("🏪 <b>{name}</b>\n{address}")
    assert ok is True
    assert err == ""


# ---------- simplify_for_user ----------


def test_simplify_strips_positive_conditional_to_body():
    assert simplify_for_user("{?chain:<i>{chain}</i>}") == "<i>{chain}</i>"


def test_simplify_strips_negative_conditional_entirely():
    assert simplify_for_user("a{?!desc:NONE}b") == "ab"


def test_simplify_handles_nested_conditionals():
    src = "{?price_today_line:{price_today_line}\n{?day_label:{day_label}\n}}"
    out = simplify_for_user(src)
    # Both conditionals stripped to their bodies.
    assert out == "{price_today_line}\n{day_label}\n"


def test_simplify_idempotent_on_clean_text():
    s = "🏪 <b>{name}</b>\n{address}"
    assert simplify_for_user(s) == s


# ---------- block specs are well-formed ----------


def test_every_block_has_unique_id():
    ids = [b.id for b in DEFAULT_BLOCKS]
    assert len(ids) == len(set(ids))


def test_get_spec_returns_correct_or_none():
    assert get_spec("header").id == "header"
    assert get_spec("definitely_not_a_block") is None


def test_working_hours_block_renders_when_value_present():
    shop = _sample_shop()
    ctx = build_context(shop, date.today(), is_tracked=False)
    out = render(assemble(default_blocks_config()), ctx)
    assert "🕒" in out
    assert "Пн-Сб 10:00–21:00" in out


def test_working_hours_block_absent_when_value_none():
    from dataclasses import replace

    shop = replace(_sample_shop(), working_hours=None)
    ctx = build_context(shop, date.today(), is_tracked=False)
    out = render(assemble(default_blocks_config()), ctx)
    assert "🕒" not in out


def test_every_default_text_renders_without_exception():
    """Each block's default text must render on a sample shop without raising.

    (We don't assert «no `{?` leftover» because the legacy render engine
    has a known limitation: `{?var:body}` bodies cannot contain other
    `{var}` placeholders. Default blocks reproduce legacy behaviour
    one-to-one — see test_assemble_default_renders_identically_to_legacy.)
    """
    shop = _sample_shop()
    ctx = build_context(shop, date.today(), is_tracked=True)
    for spec in DEFAULT_BLOCKS:
        # Just sanity: render must not raise.
        render(spec.default_text, ctx)
