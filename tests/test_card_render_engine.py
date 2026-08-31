"""Tests for the mini template engine in services/card_template.render.

Covers the bug fix where {?var:body} bodies could not contain {var}
placeholders, and the security property that user-controlled values
substituted into the template are not re-evaluated as DSL.
"""
from services.card_template import render


def _ctx(**kw) -> dict[str, str]:
    base = {"name": "", "chain": "", "address": "", "description": ""}
    base.update(kw)
    return base


def test_var_substitution():
    assert render("hi {name}!", _ctx(name="Bob")) == "hi Bob!"


def test_missing_var_substitutes_empty():
    assert render("[{nope}]", _ctx()) == "[]"


def test_conditional_truthy_keeps_body():
    assert render("a{?chain:-Z-}b", _ctx(chain="X")) == "a-Z-b"


def test_conditional_falsy_drops_body():
    assert render("a{?chain:-Z-}b", _ctx(chain="")) == "ab"


def test_negation_truthy_drops_body():
    assert render("a{?!chain:NONE}b", _ctx(chain="X")) == "ab"


def test_negation_falsy_keeps_body():
    assert render("a{?!chain:NONE}b", _ctx(chain="")) == "aNONEb"


def test_conditional_body_with_var_resolved():
    """The bug fix: body may contain {var} placeholders."""
    out = render("{?chain:<i>{chain}</i>}", _ctx(chain="Megahand"))
    assert out == "<i>Megahand</i>"


def test_conditional_body_with_var_falsy_drops_entirely():
    out = render("{?chain:<i>{chain}</i>}", _ctx(chain=""))
    assert out == ""


def test_nested_conditionals_both_truthy():
    src = "{?a:A{?b:B}}"
    assert render(src, _ctx(a="1", b="1")) == "AB"


def test_nested_conditionals_outer_truthy_inner_falsy():
    src = "{?a:A{?b:B}}"
    assert render(src, _ctx(a="1", b="")) == "A"


def test_nested_conditionals_outer_falsy():
    src = "{?a:A{?b:B}}"
    assert render(src, _ctx(a="", b="1")) == ""


def test_realistic_default_template_chain_branch():
    """Mirrors the real DEFAULT_TEMPLATE shape — was broken before fix."""
    src = "{?chain:<i>{chain}</i>\n}"
    assert render(src, _ctx(chain="Megahand")) == "<i>Megahand</i>"
    assert render(src, _ctx(chain="")) == ""


def test_realistic_nested_price_today_block():
    """Nested {?...:{?...:...}} with var inside body — was broken before."""
    src = "{?price_today_line:{price_today_line}\n{?day_label:{day_label}\n}}"
    ctx = _ctx(price_today_line="LINE", day_label="DAY")
    assert render(src, ctx) == "LINE\nDAY"


def test_unbalanced_brace_left_verbatim():
    # Malformed: no matching close brace.
    out = render("{?chain:<i>{chain}</i>", _ctx(chain="X"))
    # Should not crash; the unmatched `{?chain:` is kept verbatim.
    assert "{?chain:" in out


def test_collapse_triple_newlines():
    # Three or more `\n` collapse to two.
    src = "a\n\n\n\nb"
    assert render(src, _ctx()) == "a\n\nb"


def test_strip_leading_trailing_newlines():
    src = "\n\nhello\n\n"
    assert render(src, _ctx()) == "hello"


# ---------- security: substituted values are not re-evaluated as DSL ----------


def test_user_value_with_dsl_syntax_is_not_evaluated():
    """If shop.name = '{?chain:HACK}', it must NOT trigger conditional eval."""
    src = "<b>{name}</b>"
    out = render(src, _ctx(name="{?chain:HACK}", chain="Megahand"))
    # The literal `{?chain:HACK}` should appear in output as plain text —
    # NOT replaced by `HACK` (which would be the case if we re-evaluated).
    assert out == "<b>{?chain:HACK}</b>"


def test_user_value_with_var_placeholder_is_not_re_substituted():
    """If a value contains `{name}`, it must not be re-substituted."""
    src = "{description}"
    out = render(src, _ctx(name="REAL", description="see {name}"))
    # `{name}` inside description should remain literal, not become "REAL".
    assert out == "see {name}"
