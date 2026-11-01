import csv
from decimal import Decimal
from pathlib import Path
import unittest
from tge_parser_safe import parse_fix1, parse_complete_standard_day

FIXTURES = Path(__file__).parent / 'fixtures'

class AdmissionTests(unittest.TestCase):
    def test_real_html_matches_all_reference_prices(self):
        html = (FIXTURES / 'tge_rdn_captured_2026-11-01.html').read_text()
        actual = parse_complete_standard_day(html, '2026-11-02')
        with (FIXTURES / 'tge_fix1_2026-11-02_reference.csv').open() as f:
            expected = {int(r['hour'][1:]): Decimal(r['price_pln_mwh'])
                        for r in csv.DictReader(line for line in f if not line.startswith('#'))}
        self.assertEqual({h: Decimal(str(v)) for h,v in actual.items()}, expected)

    def test_incomplete_rejected(self):
        with self.assertRaises(ValueError):
            parse_complete_standard_day('', '2026-11-02')

    def test_dst_rejected_even_with_24_rows(self):
        for day in ('2026-10-25', '2026-03-29'):
            html = ''.join(f'<tr><td>{day}_H{h:02d}</td><td>60</td><td>100,00</td></tr>' for h in range(1,25))
            with self.subTest(day=day), self.assertRaises(ValueError):
                parse_complete_standard_day(html, day)

    def test_bad_price_rejected(self):
        for price in ('-', 'NaN', '1,2,3', '933.17'):
            with self.subTest(price=price), self.assertRaises(ValueError):
                parse_fix1(f'<tr><td>2026-11-02_H01</td><td>60</td><td>{price}</td></tr>', '2026-11-02')

    def test_unicode_spaces(self):
        for space in ('&nbsp;', '&#8239;'):
            self.assertEqual(parse_fix1(f'<tr><td>2026-11-02_H01</td><td>60</td><td>1{space}351,99</td></tr>', '2026-11-02'), {1:1351.99})

if __name__ == '__main__': unittest.main()
