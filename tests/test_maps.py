"""Unit tests for services/maps.py — Yandex Maps URL/anchor builders."""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from services.maps import (
    DEFAULT_CITY,
    yandex_maps_link_html,
    yandex_maps_url,
)


# ---------- yandex_maps_url ----------


def test_url_by_address_prefixes_default_city():
    url = yandex_maps_url("ул. Победы, 12")
    qs = parse_qs(urlparse(url).query)
    assert qs["text"] == [f"{DEFAULT_CITY}, ул. Победы, 12"]


def test_url_by_address_skips_city_prefix_if_already_present():
    url = yandex_maps_url("Севастополь, проспект Острякова, 1")
    qs = parse_qs(urlparse(url).query)
    assert qs["text"] == ["Севастополь, проспект Острякова, 1"]


def test_url_by_address_city_match_is_case_insensitive():
    url = yandex_maps_url("севастополь, какая-то улица")
    qs = parse_qs(urlparse(url).query)
    # Не дублирует «Севастополь» — оставляет исходную строку.
    assert qs["text"] == ["севастополь, какая-то улица"]


def test_url_by_address_custom_city():
    url = yandex_maps_url("ул. Ленина, 1", city="Москва")
    qs = parse_qs(urlparse(url).query)
    assert qs["text"] == ["Москва, ул. Ленина, 1"]


def test_url_with_coordinates_uses_ll_pt():
    url = yandex_maps_url("ignored", lat=44.6166, lon=33.5254)
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    # Яндекс ждёт сначала долготу.
    assert qs["ll"] == ["33.525400,44.616600"]
    assert qs["pt"] == ["33.525400,44.616600"]
    assert qs["z"] == ["17"]
    # text param должен отсутствовать.
    assert "text" not in qs


def test_url_coordinates_take_precedence_over_address():
    url = yandex_maps_url("ул. Победы, 12", lat=10.0, lon=20.0)
    assert "text=" not in url
    assert "ll=20." in url


def test_url_returns_https_scheme():
    assert yandex_maps_url("ул. Победы, 12").startswith("https://")
    assert yandex_maps_url("x", lat=1, lon=2).startswith("https://")


def test_url_empty_address_returns_empty_string():
    assert yandex_maps_url("") == ""
    assert yandex_maps_url("   ") == ""


def test_url_empty_address_with_coords_still_builds_pin():
    # Координаты — главный приоритет: даже без адреса метка строится.
    url = yandex_maps_url("", lat=1.0, lon=2.0)
    assert "ll=2.000000,1.000000" in url


def test_url_address_with_special_chars_is_quote_plus_encoded():
    url = yandex_maps_url("ул. Победы & соседи, дом #1")
    # «&» внутри адреса должен превратиться в %26 (иначе ломает query).
    assert "%26" in url
    # «#» — это якорь URL, обязан стать %23.
    assert "%23" in url
    # Сырых «&» в URL нет — у нас один query-параметр.
    assert "&" not in url


# ---------- yandex_maps_link_html ----------


def test_link_html_returns_anchor_with_escaped_text_and_href():
    html_out = yandex_maps_link_html("ул. Победы, 12")
    assert html_out.startswith('<a href="')
    assert html_out.endswith("</a>")
    assert ">ул. Победы, 12</a>" in html_out


def test_link_html_empty_address_returns_empty_string():
    assert yandex_maps_link_html("") == ""
    assert yandex_maps_link_html("   ") == ""


def test_link_html_escapes_dangerous_chars_in_address_text():
    html_out = yandex_maps_link_html('ул. "Тест" <b>X</b>')
    # html.escape переводит < > " в сущности.
    assert "&lt;" in html_out
    assert "&gt;" in html_out
    assert "&quot;" in html_out
    # Сырых тегов в тексте якоря не должно быть.
    assert "<b>" not in html_out.replace('<a href="', "").replace("</a>", "")


def test_link_html_uses_coordinates_when_present():
    html_out = yandex_maps_link_html("ул. Победы, 12", lat=44.6, lon=33.5)
    assert "ll=33.500000,44.600000" in html_out
    # text-параметра нет — мы пошли по координатной ветке.
    assert "text=" not in html_out


def test_link_html_href_is_html_attribute_safe():
    # URL с координатами содержит сырые «&» (между параметрами ll, z, pt).
    # В href= они обязаны быть экранированы как &amp;.
    html_out = yandex_maps_link_html("ул. Победы, 12", lat=44.6, lon=33.5)
    href_part = html_out.split('href="')[1].split('"')[0]
    assert "&amp;" in href_part
    # Сырых «&» (вне &amp;) в href не остаётся.
    assert href_part.count("&") == href_part.count("&amp;")
