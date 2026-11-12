import gzip
from pathlib import Path
import unittest
from tge_parser_safe import parse_complete_standard_day, parse_verified_layout

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

    def test_header_and_prices_must_come_from_their_own_sections(self):
        self.assertEqual(len(parse_complete_standard_day(self.html, '2026-11-02')), 24)
        start = self.html.index('id="rdn"')
        for section, error in (
            ('thead', 'Expected two RDN header rows'),
            ('tbody', 'Incomplete hourly Fixing I snapshot'),
        ):
            with self.subTest(section=section):
                rdn = self.html[start:]
                moved = rdn.replace(f'<{section}>', '<tfoot>', 1)
                moved = moved.replace(f'</{section}>', '</tfoot>', 1)
                self.assertNotEqual(moved, rdn)
                with self.assertRaisesRegex(ValueError, error):
                    parse_complete_standard_day(self.html[:start] + moved, '2026-11-02')

    def test_wrong_instrument_for_duplicate_hour_is_rejected_on_official_capture(self):
        from copy import deepcopy
        from lxml import html

        path = Path(__file__).parent / 'fixtures/tge_delivery_2026-11-12/tge_delivery_2026-11-12.html.gz'
        source = gzip.decompress(path.read_bytes())
        day = '2026-11-12'
        self.assertEqual(len(parse_complete_standard_day(source.decode('utf-8'), day)), 24)
        document = html.fromstring(source)
        row, = document.xpath('//table[@id="rdn"]/tbody/tr[normalize-space(td[1])=$period]',
                              period=day + '_H10')
        duplicate = deepcopy(row)
        duplicate.xpath('./td')[1].text = '15'
        row.addnext(duplicate)
        with self.assertRaisesRegex(ValueError, 'wrong instrument type: 2026-11-12_H10'):
            parse_complete_standard_day(html.tostring(document, encoding='unicode'), day)

    def test_hourly_price_must_be_a_data_cell_in_official_captures(self):
        from tge_period_labels import parse_fix1_labels

        fixtures = Path(__file__).parent / 'fixtures'
        cases = (
            (self.html, '2026-11-02', 'H01', 24, parse_complete_standard_day),
            ((fixtures / 'tge_rdn_session_2026-10-24_captured_2026-11-02.html').read_text(),
             '2026-10-25', 'H02a', 25, parse_fix1_labels),
        )
        for source, day, label, period_count, parse in cases:
            self.assertEqual(len(parse(source, day)), period_count)
            row_start = source.index(f'{day}_{label}</td>')
            price_start = source.index('<td align="right">', row_start)
            price_end = source.index('</td>', price_start)
            for start_tag, end_tag, error in (
                ('<th', '</th>', 'Expected td in RDN tbody'),
                ('<td', '</th>', 'Mismatched RDN cell closing tag'),
            ):
                with self.subTest(day=day, end_tag=end_tag, start_tag=start_tag):
                    changed = (source[:price_start] + start_tag + source[price_start + 3:price_end]
                               + end_tag + source[price_end + 5:])
                    with self.assertRaisesRegex(ValueError, error):
                        parse(changed, day)

    def test_duplicate_fixing_i_group_rejected(self):
        html = self.html.replace('>Notowania ciągłe</th>', '>Fixing I</th>', 1)
        self.assertNotEqual(html, self.html)
        with self.assertRaisesRegex(ValueError, 'Fixing I columns changed'):
            parse_verified_layout(html, '2026-11-02')
