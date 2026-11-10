"""SHADOW tests: screenshot-derived values are examples, not HTML validation."""
import unittest
from tge_hourly_fixings_shadow import parse_hourly_fixings

class TestHourlyFixings(unittest.TestCase):
    def test_quarters_ignored_and_two_fixings_separate(self):
        html='<table><tr><td>2026-11-10_H01</td><td>60</td><td>987,47</td><td>-</td><td>900,00</td></tr><tr><td>2026-11-10_Q00:15</td><td>15</td><td>869,63</td><td>12</td><td>800,00</td></tr><tr><td>2026-11-10_H02</td><td>60</td><td>1 025,83</td><td>-</td><td>950,00</td></tr></table>'
        r=parse_hourly_fixings(html,'2026-11-10',fixing2_column=4)
        self.assertEqual(r['fixing_i'],{1:987.47,2:1025.83})
        self.assertEqual(r['fixing_ii'],{1:900.,2:950.})
    def test_fixing_ii_not_guessed(self):
        html='<tr><td>2026-11-10_H01</td><td>60</td><td>987,47</td></tr>'
        self.assertEqual(parse_hourly_fixings(html,'2026-11-10')['fixing_ii'],{})
    def test_duplicate_rejected(self):
        html='<tr><td>2026-11-10_H01</td><td>60</td><td>987,47</td></tr>'*2
        with self.assertRaises(ValueError): parse_hourly_fixings(html,'2026-11-10')
    def test_missing_column_rejected(self):
        html='<tr><td>2026-11-10_H01</td><td>60</td><td>987,47</td></tr>'
        with self.assertRaises(ValueError): parse_hourly_fixings(html,'2026-11-10',fixing2_column=4)
if __name__=='__main__': unittest.main()
