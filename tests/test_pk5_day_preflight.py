"""PK5 day admission rejects unverified DST before metadata/payload I/O."""
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from shadow_pk5_snapshot import pk5_standard_periods, prepare_pk5_snapshot
from shadow_scoring import expected_periods
from shadow_source_history import load_pk5_capture, load_pk5_snapshot
from tools.capture_pk5_validation import capture

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / 'tests/fixtures/pse_pk5_delivery_2026-11-27_thirteenth'
DST_ERROR = 'PK5 DST mapping requires separate verification'


class PK5DayPreflightTests(unittest.TestCase):
    def test_both_dst_days_reject_before_source_io_in_all_entry_points(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            sentinel = directory / 'sentinel'
            sentinel.write_bytes(b'unchanged')
            for day, count in ((date(2026, 3, 29), 23), (date(2026, 10, 25), 25)):
                self.assertEqual(len(expected_periods(day)), count)
                with self.subTest(day=day), patch(
                    'pathlib.Path.open', side_effect=AssertionError('Unexpected metadata I/O')
                ), patch(
                    'tools.capture_pk5_validation._open_with_retry',
                    side_effect=AssertionError('Unexpected HTTP request')
                ) as request, patch(
                    'tools.capture_pk5_validation._write_new_evidence',
                    side_effect=AssertionError('Unexpected evidence write')
                ) as write:
                    for loader in (load_pk5_capture, load_pk5_snapshot):
                        with self.assertRaisesRegex(ValueError, DST_ERROR):
                            loader(directory / 'missing.json', day=day)
                    with self.assertRaisesRegex(ValueError, DST_ERROR):
                        pk5_standard_periods(day)
                    with self.assertRaisesRegex(ValueError, DST_ERROR):
                        prepare_pk5_snapshot(
                            raw=b'not JSON', day=day.isoformat(), source_url='invalid',
                            retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                            retrieval_completed_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
                    with self.assertRaisesRegex(ValueError, DST_ERROR):
                        capture(day, directory)
                    request.assert_not_called()
                    write.assert_not_called()
            self.assertEqual(list(directory.iterdir()), [sentinel])
            self.assertEqual(sentinel.read_bytes(), b'unchanged')

    def test_dst_cli_has_no_partial_json_or_traceback_for_either_path_order(self):
        with TemporaryDirectory() as temporary:
            malformed = Path(temporary) / 'malformed.json'
            malformed.write_bytes(b'not JSON')
            missing = Path(temporary) / 'missing.json'
            for day in ('2026-03-29', '2026-10-25'):
                for ordered in ((missing, malformed), (malformed, missing)):
                    result = subprocess.run(
                        [sys.executable, str(ROOT / 'shadow_pk5_audit.py'), '--day', day,
                         '--require-selection', *map(str, ordered)],
                        capture_output=True, text=True)
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(result.stdout, '')
                    self.assertIn(DST_ERROR, result.stderr)
                    self.assertNotIn('Traceback', result.stderr)
            self.assertEqual(malformed.read_bytes(), b'not JSON')
            self.assertFalse(missing.exists())

    def test_standard_day_keeps_original_identity_rows_and_source_bytes(self):
        day = date(2026, 11, 27)
        files = {p: p.read_bytes() for p in DIRECTORY.iterdir() if p.is_file()}
        metadata = json.loads((DIRECTORY / 'capture.json').read_bytes())
        evidence = load_pk5_capture(DIRECTORY / 'capture.json', day=day)
        snapshot = load_pk5_snapshot(DIRECTORY / 'capture.json', day=day)
        self.assertEqual(pk5_standard_periods(day), expected_periods(day))
        self.assertEqual(len(snapshot['rows']), 24)
        self.assertEqual(snapshot['id'],
                         'pk5-capture-sha256:d0fb094ed1fa1e5291259f8ae7161738d4078134be5cd6d9d4c013875cbedba9')
        self.assertEqual(snapshot['raw_sha256'], metadata['raw_sha256'])
        self.assertEqual(prepare_pk5_snapshot(day=day.isoformat(), **evidence), snapshot)
        self.assertEqual(snapshot['rows'][0]['jwcd_mw'], Decimal('13684.00000'))
        self.assertEqual(snapshot['rows'][-1]['jwcd_mw'], Decimal('14001.00000'))
        self.assertEqual({p: p.read_bytes() for p in files}, files)


if __name__ == '__main__':
    unittest.main()
