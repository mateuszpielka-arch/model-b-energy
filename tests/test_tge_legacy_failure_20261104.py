"""Reproduce deployed collector commit 657fcc4 parsing bug on CONSTRUCTED HTML.

The legacy regex below is transcribed from deployed tge_collector.py at 657fcc4.
This is NOT a raw TGE HTML capture; it proves a deterministic failure mode.
"""
import re
import unittest
from test_tge_fix1_20261104 import make_full_table, load_reference
from tge_parser_safe import parse_fix1


def legacy_parse_fix1_657fcc4(html, delivery_date):
    rows = {}
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'\s+', ' ', text)
    for h in range(1, 25):
        key = f'{delivery_date}_H{h:02d}'
        p = text.find(key)
        if p < 0:
            continue
        chunk = text[p:p+1800]
        after = chunk[len(key):]
        vals = re.findall(r'[-−]?\d+(?:[ .]\d{3})*(?:,\d+)?', after)
        for raw in vals:
            raw = raw.replace('−', '-')
            try:
                v = float(raw.replace('\xa0', '').replace(' ', '').replace(',', '.'))
            except ValueError:
                continue
            if -5000 <= v <= 20000:
                rows[h] = v
                break
    return rows


class LegacyFailureReproduction(unittest.TestCase):
    def test_deployed_legacy_parser_returns_60_or_2026_not_prices(self):
        reference = load_reference()
        html = make_full_table(reference)
        legacy = legacy_parse_fix1_657fcc4(html, '2026-11-04')
        strict = parse_fix1(html, '2026-11-04')
        self.assertEqual(len(legacy), 24)
        self.assertEqual(set(legacy.values()), {60.0, 2026.0})
        self.assertNotEqual(legacy, reference)
        self.assertEqual(strict, reference)

if __name__ == '__main__':
    unittest.main()
