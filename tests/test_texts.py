import asyncio
import time

import pytest

from services import texts


@pytest.fixture(autouse=True)
def _clean_cache():
    texts.invalidate()
    yield
    texts.invalidate()


def _prime_cache(key: str, value: str) -> None:
    texts._CACHE[key] = (time.time(), value)


class TestExtractPlaceholders:
    def test_no_placeholders(self) -> None:
        assert texts.extract_placeholders("Привет всем") == set()

    def test_single(self) -> None:
        assert texts.extract_placeholders("Привет, {name}!") == {"name"}

    def test_multiple(self) -> None:
        assert texts.extract_placeholders("{a} и {b}, потом {a}") == {"a", "b"}

    def test_ignores_invalid_braces(self) -> None:
        assert texts.extract_placeholders("{Bad} {0} {} ok") == set()


class TestValidate:
    def test_accepts_default(self) -> None:
        for key, val in texts.DEFAULTS.items():
            ok, err = texts.validate(key, val)
            assert ok, f"default for {key} failed: {err}"

    def test_rejects_empty(self) -> None:
        ok, err = texts.validate("menu.btn.help", "")
        assert not ok
        assert "пуст" in err.lower()

    def test_rejects_too_long(self) -> None:
        ok, err = texts.validate("help.text", "x" * 5000)
        assert not ok
        assert "длинн" in err.lower()

    def test_rejects_unknown_key(self) -> None:
        ok, _ = texts.validate("nope.nope", "anything")
        assert not ok

    def test_rejects_unknown_placeholder(self) -> None:
        ok, err = texts.validate("catalog.found", "Найдено: {count} из {total}")
        assert not ok
        assert "{total}" in err

    def test_accepts_correct_placeholder(self) -> None:
        ok, _ = texts.validate("catalog.found", "Найдено магазинов: {count}")
        assert ok

    def test_rejects_placeholder_when_none_allowed(self) -> None:
        ok, err = texts.validate("menu.btn.help", "{anything}")
        assert not ok
        assert "{anything}" in err

    def test_optional_placeholder_can_be_omitted(self) -> None:
        # If allowed has {count} but new value doesn't use it — that's fine.
        ok, _ = texts.validate("catalog.found", "Найдено магазинов")
        assert ok


class TestRenderPreview:
    def test_no_placeholders(self) -> None:
        assert texts.render_preview("menu.btn.help", "Помощь!") == "Помощь!"

    def test_substitutes_placeholders(self) -> None:
        rendered = texts.render_preview("catalog.found", "Найдено: {count}")
        assert "{count}" not in rendered
        assert "5" in rendered  # PREVIEW_CTX["count"] == "5"


def test_t_returns_default_when_no_override() -> None:
    _prime_cache("menu.btn.help", texts.DEFAULTS["menu.btn.help"])
    val = asyncio.run(texts.t("menu.btn.help"))
    assert val == "❓ Помощь"


def test_t_substitutes_placeholders() -> None:
    _prime_cache("catalog.found", texts.DEFAULTS["catalog.found"])
    val = asyncio.run(texts.t("catalog.found", count=42))
    assert "42" in val
    assert "{count}" not in val


def test_t_uses_override_from_cache() -> None:
    _prime_cache("menu.btn.help", "🆘 ПОМОЩЬ")
    val = asyncio.run(texts.t("menu.btn.help"))
    assert val == "🆘 ПОМОЩЬ"


def test_t_unknown_key_returns_marker() -> None:
    val = asyncio.run(texts.t("nope.nope"))
    assert "[nope.nope]" == val


def test_t_falls_back_to_default_on_format_error() -> None:
    _prime_cache("catalog.found", "Найдено: {missing}")
    val = asyncio.run(texts.t("catalog.found", count=3))
    # Falls back to default rendered with valid kwargs.
    assert "3" in val
    assert "{missing}" not in val
    assert "{count}" not in val


class TestGroupsCoverage:
    def test_every_default_is_in_a_group(self) -> None:
        all_grouped = {k for _, _, keys in texts.GROUPS for k in keys}
        missing = set(texts.DEFAULTS) - all_grouped
        assert not missing, f"keys not in any group: {missing}"

    def test_every_default_has_description(self) -> None:
        missing = set(texts.DEFAULTS) - set(texts.DESCRIPTIONS)
        assert not missing, f"keys without description: {missing}"

    def test_groups_only_reference_real_keys(self) -> None:
        for _, _, keys in texts.GROUPS:
            for k in keys:
                assert k in texts.DEFAULTS, f"group references unknown key: {k}"
