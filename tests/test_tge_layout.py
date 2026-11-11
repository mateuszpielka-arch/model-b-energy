import gzip
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

    def test_missing_price_cell_cannot_shift_volume_into_price_column(self):
        start = self.html.index('<td align="right">', self.html.index('2026-11-02_H01'))
        end = self.html.index('</td>', start) + len('</td>')
        volume_end = self.html.index('</td>', end)
        html = self.html[:start] + '<td>2492,98' + self.html[volume_end:]
        with self.assertRaisesRegex(ValueError, 'row width does not match header'):
            parse_verified_layout(html, '2026-11-02')

    def test_split_fixing_i_header_is_rejected_on_november_12_capture(self):
        path = Path(__file__).parent / 'fixtures/tge_delivery_2026-11-12/tge_delivery_2026-11-12.html.gz'
        source = gzip.decompress(path.read_bytes()).decode('utf-8')
        original = '<th colspan="2" align="center">Fixing I</th>'
        self.assertEqual(source.count(original), 1)
        self.assertEqual(len(parse_verified_layout(source, '2026-11-12')), 24)
        split = source.replace(original, '<th>Fixing I</th><th>Fixing I</th>', 1)
        with self.assertRaisesRegex(ValueError, 'Fixing I columns changed'):
            parse_verified_layout(split, '2026-11-12')

    def test_duplicate_fixing_i_group_rejected(self):
        html = self.html.replace('>Notowania ciągłe</th>', '>Fixing I</th>', 1)
        self.assertNotEqual(html, self.html)
        with self.assertRaisesRegex(ValueError, 'Fixing I columns changed'):
            parse_verified_layout(html, '2026-11-02')
