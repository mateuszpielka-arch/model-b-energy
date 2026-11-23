import csv
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import unittest

from shadow_history_audit import audit_rows, read_csv
from shadow_ingest_manifest import load_batches
from shadow_manifest_audit import audit_manifests
from shadow_scoring import expected_periods
from tge_parser_safe import parse_complete_standard_day
from tools.capture_tge_validation import extract_reference


FIXTURES = Path(__file__).parent / 'fixtures'
DIRECTORY = FIXTURES / 'tge_delivery_2026-11-23'
DAY = date(2026, 11, 23)


class November23SourcesTests(unittest.TestCase):
    def test_real_fix1_parser_xpath_reference_hashes_and_explicit_utc_agree(self):
        entry, = json.loads((DIRECTORY / 'manifest.json').read_text())['entries']
        (batch, raw), = load_batches(DIRECTORY / 'manifest.json')
        reference = (DIRECTORY / entry['reference_file']).read_bytes()
        self.assertEqual((entry['http_status'], entry['hours'], len(raw)), (200, 24, 426420))
        self.assertEqual(sha256(raw).hexdigest(),
                         'ac5c3139d9e00c323f48475d899cda7af87f3c7114773c06da4fa62fdf6ab194')
        self.assertEqual(sha256(reference).hexdigest(),
                         '68784f149172d92dcefd115c8ead9f522b9264cb71c7d1031b4fcd2539f0bebe')
        self.assertEqual(entry['retrieved_at_utc'], '2026-11-23T05:13:41.794372+00:00')
        rows = list(csv.DictReader(reference.decode().splitlines()))
        independent = extract_reference(raw, DAY.isoformat())
        parsed = parse_complete_standard_day(raw.decode(), DAY.isoformat(), exact=True)
        self.assertEqual(independent, [(row['hour'], Decimal(row['price_pln_mwh'])) for row in rows])
        self.assertEqual(parsed, dict(enumerate((price for _, price in independent), 1)))
        self.assertEqual([row['period_start_utc'] for row in rows],
                         [period.isoformat() for period in expected_periods(DAY)])
        self.assertEqual(batch.rows, tuple(zip(expected_periods(DAY),
                                              (price for _, price in independent), strict=True)))
        self.assertEqual((parsed[1], parsed[24]), (Decimal('597.82'), Decimal('468.67')))

    def test_price_only_capture_extends_consensus_but_does_not_admit_training_history(self):
        paths = [FIXTURES / 'tge_delivery_2026-11-22/manifest.json',
                 DIRECTORY / 'manifest.json']
        files = {path for manifest in paths for path in manifest.parent.iterdir() if path.is_file()}
        before = {path: sha256(path.read_bytes()).hexdigest() for path in files}
        report = audit_manifests(paths, start_day=date(2026, 11, 22), end_day=DAY)
        self.assertEqual(report, audit_manifests(
            list(reversed(paths)), start_day=date(2026, 11, 22), end_day=DAY))
        self.assertEqual((report['delivery_days'], report['unique_delivery_periods'],
                          report['days_with_price_conflicts']), (2, 48, 0))
        self.assertEqual(report['calendar_coverage']['missing_days'], 0)
        self.assertEqual(report['calendar_coverage']['by_hours']['24']['observed_days'], 2)
        readiness = audit_rows(read_csv(DIRECTORY / 'tge_delivery_2026-11-23.csv'))
        self.assertEqual(readiness['model_ready_days'], 0)
        self.assertEqual(readiness['complete_pair_days'], 0)
        day, = readiness['days']
        self.assertEqual(day['valid_fix1_rows'], 24)
        self.assertEqual(day['valid_jwcd_rows'], 0)
        self.assertIn('MISSING_OR_INVALID_JWCD', day['issues'])
        self.assertEqual({path: sha256(path.read_bytes()).hexdigest() for path in files}, before)


if __name__ == '__main__':
    unittest.main()
