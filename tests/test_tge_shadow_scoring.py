"""Offline regression scoring using FROZEN #42 and independent 2026-11-03 TGE reference."""
import csv
import unittest
from pathlib import Path
from tge_shadow_scoring import score_verified

fixture = Path(__file__).parent / 'fixtures' / 'tge_fix1_2026-11-03_frozen42_scoring.csv'
with fixture.open(encoding='utf-8') as f:
    rows = list(csv.DictReader(line for line in f if not line.startswith('#')))
forecast = {int(row['hour'][1:]): float(row['frozen42_pln_mwh']) for row in rows}
actual = {int(row['hour'][1:]): float(row['tge_fix1_pln_mwh']) for row in rows}

class ShadowScoringTests(unittest.TestCase):
    def test_frozen42_reference_metrics(self):
        s = score_verified(forecast, actual, dict(actual))
        for name, exp in {'global': (321.50,25.48,25.70,-316.97),
                          'H11-H16': (465.37,35.22,35.18,-465.37),
                          'H18-H22': (197.93,13.91,14.02,-176.21)}.items():
            for k,v in zip(('mae_pln_mwh','mape_pct','wape_pct','bias_pln_mwh'),exp):
                self.assertAlmostEqual(s[name][k],v,delta=0.006)
    def test_reject_unverified_actuals(self):
        wrong = dict(actual);wrong[8] = 60.0
        with self.assertRaisesRegex(ValueError,'reference'):
            score_verified(forecast,wrong,dict(actual))
    def test_reject_missing_hour(self):
        incomplete = dict(actual);incomplete.pop(24)
        with self.assertRaisesRegex(ValueError,'24 unique'):
            score_verified(forecast,incomplete,dict(actual))
    def test_reject_zero_actual_mape(self):
        zero = dict(actual);zero[12]=0
        with self.assertRaisesRegex(ValueError,'MAPE undefined'):
            score_verified(forecast,zero,dict(zero))

if __name__ == '__main__': unittest.main()
