"""Offline regression against independent TGE reference values; HTML is constructed, NOT captured from TGE."""
import csv
import io
import unittest
from tge_fix1_staging_guard import validate_staging_html

from pathlib import Path
fixture = Path(__file__).parent / "fixtures" / "tge_fix1_2026-11-02_reference.csv"
with fixture.open(encoding="utf-8") as f:
    prices = {int(row['hour'][1:]): float(row['price_pln_mwh']) for row in csv.DictReader(line for line in f if not line.startswith('#'))}


def make_html(prices=prices, *, wrong_header=False, bad_type=False, missing=None, duplicate=False):
    col = 'Wolumen [MW]' if wrong_header else 'Kurs [PLN/MWh]'
    rows = ['<table><tr><th colspan="3">Fixing I</th></tr>',
            f'<tr><th>Data dostawy</th><th>Typ instrumentu</th><th>{col}</th></tr>']
    for hour, price in prices.items():
        if hour == missing:
            continue
        typ = '15' if bad_type and hour == 8 else '60'
        rows.append(f'<tr><td>2026-11-02_H{hour:02d}</td><td>{typ}</td><td>{price:,.2f}</td></tr>'.replace(',', ' ').replace('.', ','))
    if duplicate:
        rows.append('<tr><td>2026-11-02_H01</td><td>60</td><td>687,34</td></tr>')
    rows.append('<tr><td>2026-11-02_Q01:15</td><td>15</td><td>9999,00</td></tr></table>')
    return ''.join(rows)


class StagingGuardTests(unittest.TestCase):
    def test_official_reference_24_hour_values(self):
        self.assertEqual(validate_staging_html(make_html(), '2026-11-02', prices), prices)
    def test_reject_changed_column_header(self):
        with self.assertRaisesRegex(ValueError, 'header'):
            validate_staging_html(make_html(wrong_header=True), '2026-11-02')
    def test_reject_wrong_instrument(self):
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            validate_staging_html(make_html(bad_type=True), '2026-11-02')
    def test_reject_missing_hour(self):
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            validate_staging_html(make_html(missing=14), '2026-11-02')
    def test_reject_duplicate_hour(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            validate_staging_html(make_html(duplicate=True), '2026-11-02')
    def test_reject_bad_reference(self):
        ref = dict(prices); ref[16] = 60
        with self.assertRaisesRegex(ValueError, 'Mismatch'):
            validate_staging_html(make_html(), '2026-11-02', ref)
    def test_reject_2026_as_price(self):
        ref = dict(prices)
        for h in range(1, 13): ref[h] = 2026
        with self.assertRaisesRegex(ValueError, 'Suspect'):
            validate_staging_html(make_html(ref), '2026-11-02')
    def test_reject_dst_25_hour_day(self):
        with self.assertRaisesRegex(ValueError, 'DST'):
            validate_staging_html(make_html(), '2026-10-25')
    def test_reject_dst_23_hour_day(self):
        with self.assertRaisesRegex(ValueError, 'DST'):
            validate_staging_html(make_html(), '2027-03-28')


if __name__ == '__main__':
    unittest.main()
