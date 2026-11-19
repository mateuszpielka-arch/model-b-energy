"""SHADOW-only tests for strict TGE RDN Fixing I admission; no network or DB."""
import unittest
from decimal import Decimal
from tge_parser_safe import parse_complete_standard_day

DAY = "2026-11-19"

def page(prices=None, *, duplicate=False, instrument="60"):
    prices = prices or {h: f"{100+h},25" for h in range(1, 25)}
    body = "".join(
        f"<tr><td>{DAY}_H{h:02d}</td><td>{instrument}</td><td>{price}</td><td>1</td></tr>"
        for h, price in prices.items()
    )
    if duplicate:
        body += f"<tr><td>{DAY}_H01</td><td>60</td><td>101,25</td><td>1</td></tr>"
    return ('<table id="rdn"><thead><tr><th></th><th></th><th colspan="2">Fixing I</th></tr>'
            '<tr><th>Data dostawy</th><th>Typ instrumentu</th>'
            '<th>Kurs [PLN/MWh]</th><th>Wolumen [MW]</th></tr></thead>'
            '<tbody>' + body + '</tbody></table>')

class StrictAdmissionTests(unittest.TestCase):
    def test_complete_day_exact_prices(self):
        rows = parse_complete_standard_day(page(), DAY, exact=True)
        self.assertEqual(len(rows), 24)
        self.assertEqual(rows[1], Decimal("101.25"))
        self.assertEqual(rows[24], Decimal("124.25"))

    def test_incomplete_day_rejected(self):
        with self.assertRaises(ValueError):
            parse_complete_standard_day(page({1: "101,25"}), DAY)

    def test_duplicate_hour_rejected(self):
        with self.assertRaises(ValueError):
            parse_complete_standard_day(page(duplicate=True), DAY)

    def test_wrong_instrument_rejected(self):
        with self.assertRaises(ValueError):
            parse_complete_standard_day(page(instrument="15"), DAY)

    def test_missing_official_table_rejected(self):
        with self.assertRaises(ValueError):
            parse_complete_standard_day(page().replace('id="rdn"', 'id="other"'), DAY)

    def test_negative_price_and_thousands(self):
        prices = {h: f"{100+h},25" for h in range(1,25)}
        prices[11] = "1 351,99"
        prices[12] = "−13,46"
        rows = parse_complete_standard_day(page(prices), DAY, exact=True)
        self.assertEqual(rows[11], Decimal("1351.99"))
        self.assertEqual(rows[12], Decimal("-13.46"))

    def test_dst_day_blocked(self):
        with self.assertRaises(ValueError):
            parse_complete_standard_day(page().replace(DAY, "2026-10-25"), "2026-10-25")

if __name__ == "__main__":
    unittest.main()
