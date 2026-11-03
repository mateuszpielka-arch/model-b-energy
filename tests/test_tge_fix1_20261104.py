"""SHADOW 2026-11-04: official-reference regression on CONSTRUCTED 17-column HTML.

This is NOT a raw TGE HTML capture. No network, DB, or production side effects.
Source of reference prices: https://tge.pl/energia-elektryczna-rdn (viewed 2026-11-03).
"""
import csv
import unittest
from pathlib import Path
from tge_fix1_staging_guard import validate_staging_html
from tge_shadow_scoring import score_verified

FIXTURES = Path(__file__).parent / 'fixtures'

def load_reference():
    with (FIXTURES / 'tge_fix1_2026-11-04_reference.csv').open(encoding='utf-8', newline='') as fh:
        return {int(row['hour'][1:]): float(row['price_pln_mwh']) for row in csv.DictReader(fh)}


def make_full_table(prices, *, shift=False, instrument_as_price=False, missing_hour=None, duplicate=False):
    """Mimic TGE's published 17-column table and interleaved 15-minute rows."""
    price_header = 'Wolumen [MW]' if shift else 'Kurs [PLN/MWh]'
    html = ['<h2>Kontrakty dla dostawy w dniu 04-11-2026</h2>',
            '<table><thead><tr><th></th><th></th><th>Fixing I</th>' + '<th></th>'*14 + '</tr>',
            '<tr><th>Data dostawy</th><th>Typ instrumentu</th><th>' + price_header + '</th>' + '<th>Wolumen [MW]</th>'*14 + '</tr></thead><tbody>']
    for hour in range(1, 25):
        if hour == missing_hour:
            continue
        val = '60' if instrument_as_price else f'{prices[hour]:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', ' ')
        html.append(f'<tr><td>2026-11-04_H{hour:02d}</td><td>60</td><td>{val}</td>' + '<td>-</td>'*14 + '</tr>')
        html.append(f'<tr><td>2026-11-04_Q{hour-1:02d}:15</td><td>15</td><td>9 999,99</td>' + '<td>1 111,11</td>'*14 + '</tr>')
    if duplicate:
        html.append('<tr><td>2026-11-04_H01</td><td>60</td><td>983,00</td>' + '<td>-</td>'*14 + '</tr>')
    html.append('</tbody></table>')
    return ''.join(html)

class RealReferenceNov04Tests(unittest.TestCase):
    def setUp(self):
        self.reference = load_reference()

    def test_24_official_reference_values_and_index(self):
        self.assertEqual(len(self.reference), 24)
        self.assertAlmostEqual(sum(self.reference.values())/24, 1355.43, places=2)
        self.assertEqual(validate_staging_html(make_full_table(self.reference), '2026-11-04', self.reference), self.reference)

    def test_17_columns_and_quarter_hour_rows_are_not_misread(self):
        actual = validate_staging_html(make_full_table(self.reference), '2026-11-04', self.reference)
        self.assertEqual(actual[16], 1959.00)
        self.assertEqual(actual[20], 1986.92)
        self.assertNotIn(9999.99, actual.values())

    def test_shifted_price_header_rejected(self):
        with self.assertRaisesRegex(ValueError, 'header'):
            validate_staging_html(make_full_table(self.reference, shift=True), '2026-11-04', self.reference)

    def test_instrument_type_as_price_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Suspect'):
            validate_staging_html(make_full_table(self.reference, instrument_as_price=True), '2026-11-04', self.reference)

    def test_missing_and_duplicate_hour_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            validate_staging_html(make_full_table(self.reference, missing_hour=24), '2026-11-04', self.reference)
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            validate_staging_html(make_full_table(self.reference, duplicate=True), '2026-11-04', self.reference)

    def test_scoring_frozen43_matches_independent_metrics(self):
        with (FIXTURES / 'tge_fix1_2026-11-04_frozen43_scoring.csv').open(encoding='utf-8', newline='') as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 24)
        forecast = {int(row['hour'][1:]): float(row['forecast_frozen43_pln_mwh']) for row in rows}
        actual = {int(row['hour'][1:]): float(row['actual_fix1_pln_mwh']) for row in rows}
        metrics = score_verified(forecast, actual, self.reference)
        self.assertAlmostEqual(metrics['global']['mae_pln_mwh'], 409.4483333333, places=5)
        self.assertAlmostEqual(metrics['global']['mape_pct'], 29.3635495784, places=5)
        self.assertAlmostEqual(metrics['global']['wape_pct'], 30.2079285837, places=5)
        self.assertAlmostEqual(metrics['global']['bias_pln_mwh'], -409.4483333333, places=5)
        self.assertAlmostEqual(metrics['H11-H16']['mae_pln_mwh'], 459.045, places=5)
        self.assertAlmostEqual(metrics['H18-H22']['mae_pln_mwh'], 484.134, places=5)

if __name__ == '__main__':
    unittest.main()
