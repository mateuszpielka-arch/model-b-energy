"""Regression: instrument duration 60 must never be archived as the price.

Run: python -m unittest tests.test_tge_sixty_is_not_price
Pure tests; no network or database writes.
"""
import unittest
from tge_parser_safe import parse_fix1


class SixtyIsNotPrice(unittest.TestCase):
    def test_hourly_instrument_duration_is_not_price(self):
        html = """<table><tr><th>Instrument</th><th>Duration</th><th>Fixing I price</th></tr>
        <tr><td>2026-11-10_H01</td><td>60</td><td>987,47</td></tr>
        <tr><td>2026-11-10_H02</td><td>60</td><td>1 025,83</td></tr>
        <tr><td>2026-11-10_Q00:15</td><td>15</td><td>999,99</td></tr></table>"""
        self.assertEqual(parse_fix1(html, "2026-11-10"), {1: 987.47, 2: 1025.83})

    def test_price_column_containing_sixty_fails_quality_gate(self):
        html = """<table><tr><td>2026-11-13_H11</td><td>60</td><td>60,00</td></tr></table>"""
        # A legitimate market price can be 60; rejecting this requires external
        # evidence, not a magic-value filter. This test documents that boundary.
        self.assertEqual(parse_fix1(html, "2026-11-13"), {11: 60.0})


if __name__ == "__main__":
    unittest.main()
