"""Regression tests for fail-closed duplicate handling in SHADOW parser."""
import pytest
from tge_parser_safe import parse_fix1


def test_duplicate_hour_with_malformed_first_price_is_rejected():
    html = """<table>
    <tr><td>2026-11-06_H01</td><td>60</td><td>not-a-price</td></tr>
    <tr><td>2026-11-06_H01</td><td>60</td><td>900,00</td></tr>
    </table>"""
    with pytest.raises(ValueError, match="Duplicate hourly key"):
        parse_fix1(html, "2026-11-06")


def test_single_malformed_price_is_not_silently_replaced():
    html = """<table><tr>
    <td>2026-11-06_H01</td><td>60</td><td>not-a-price</td>
    </tr></table>"""
    assert parse_fix1(html, "2026-11-06") == {}


def test_valid_single_price_is_parsed():
    html = """<table><tr>
    <td>2026-11-06_H01</td><td>60</td><td>900,00</td>
    </tr></table>"""
    assert parse_fix1(html, "2026-11-06") == {1: 900.0}
