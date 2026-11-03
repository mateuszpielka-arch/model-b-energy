from pathlib import Path
import unittest
from tge_parser_safe import parse_verified_layout

class LayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (Path(__file__).parent / 'fixtures/tge_rdn_captured_2026-11-01.html').read_text()

    def test_real_layout(self):
        self.assertEqual(len(parse_verified_layout(self.html, '2026-11-02')), 24)

    def test_wrong_table_rejected(self):
        with self.assertRaises(ValueError):
            parse_verified_layout(self.html.replace('id="rdn"', 'id="mc"'), '2026-11-02')

    def test_wrong_fixing_rejected(self):
        with self.assertRaises(ValueError):
            parse_verified_layout(self.html.replace('>Fixing I</th>', '>Fixing II</th>'), '2026-11-02')

    def test_swapped_price_and_volume_headers_rejected(self):
        with self.assertRaises(ValueError):
            parse_verified_layout(self.html.replace('>Kurs [PLN/MWh]</th>', '>Wolumen [MW]</th>'), '2026-11-02')

    def test_duplicate_table_rejected(self):
        with self.assertRaises(ValueError):
            parse_verified_layout(self.html + self.html, '2026-11-02')

    def test_truncated_table_rejected(self):
        end = self.html.index('2026-11-02_H12')
        with self.assertRaises(ValueError):
            parse_verified_layout(self.html[:end], '2026-11-02')

    def test_other_market_rows_ignored(self):
        foreign = '<table id="mc"><tr><td>2026-11-02_H01</td><td>60</td><td>1,00</td></tr></table>'
        self.assertEqual(parse_verified_layout(foreign + self.html, '2026-11-02')[1], 687.34)

    def test_merged_data_cell_rejected(self):
        label = '2026-11-02_H01</td>'
        start = self.html.rindex('<td', 0, self.html.index(label))
        html = self.html[:start] + self.html[start:].replace('<td', '<td colspan="2"', 1)
        with self.assertRaisesRegex(ValueError, 'RDN data cell colspan'):
            parse_verified_layout(html, '2026-11-02')
