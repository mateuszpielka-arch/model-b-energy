"""Isolated parser tests; no network or database calls."""
import unittest
from tge_parser_safe import parse_fix1

class TgeParserTests(unittest.TestCase):
    def test_hourly_price_not_instrument_type(self):
        html = '<table><tr><td>2026-10-30_H01</td><td>60</td><td>933,17</td></tr><tr><td>2026-10-30_H02</td><td>60</td><td>984,99</td></tr></table>'
        self.assertEqual(parse_fix1(html, '2026-10-30'), {1:933.17, 2:984.99})

    def test_thousand_separators_negative_and_quarter_hours(self):
        html = '<table><tr><td>2026-10-30_H07</td><td>60</td><td><span>1 351,99</span></td></tr><tr><td>2026-10-30_Q06:15</td><td>15</td><td>1 211,98</td></tr><tr><td>2026-10-30_H13</td><td>60</td><td>−13,46</td></tr></table>'
        self.assertEqual(parse_fix1(html, '2026-10-30'), {7:1351.99,13:-13.46})

    def test_duplicate_hour_fails_closed(self):
        html = '<tr><td>2026-10-30_H01</td><td>60</td><td>933,17</td></tr>'*2
        with self.assertRaises(ValueError): parse_fix1(html, '2026-10-30')

    def test_does_not_cross_into_adjacent_day(self):
        html = '<tr><td>2026-10-30_H01</td><td>60</td><td>933,17</td></tr><tr><td>2026-10-31_H02</td><td>60</td><td>888,88</td></tr>'
        self.assertEqual(parse_fix1(html, '2026-10-30'), {1:933.17})

    def test_full_24_hour_day(self):
        html = '<table>'+''.join(f'<tr><td>2026-10-30_H{h:02d}</td><td>60</td><td>{100+h},00</td></tr>' for h in range(1,25))+'</table>'
        rows=parse_fix1(html, '2026-10-30')
        self.assertEqual(len(rows),24)
        self.assertEqual(rows[24],124.)

if __name__ == '__main__': unittest.main()
