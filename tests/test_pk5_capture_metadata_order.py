"""Malformed PK5 capture metadata is rejected before reading compressed evidence."""
from datetime import date
import gzip
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from shadow_pk5_snapshot import prepare_pk5_snapshot
from shadow_source_history import load_pk5_capture, load_pk5_snapshot

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / 'tests/fixtures/pse_pk5_delivery_2026-11-26_eighth'
DAY = date(2026, 11, 26)


class PK5MetadataOrderTests(unittest.TestCase):
    def test_missing_or_malformed_timestamps_and_wrong_day_never_open_gzip(self):
        original = json.loads((DIRECTORY / 'capture.json').read_text())
        with TemporaryDirectory() as temporary:
            target = Path(temporary)
            path = target / 'capture.json'
            raw_path = target / original['raw_file']
            raw_path.write_bytes((DIRECTORY / original['raw_file']).read_bytes())
            cases = [({**original, 'delivery_date': '2026-11-25'}, 'delivery differs')]
            for field in ('retrieved_at_utc', 'retrieval_completed_at_utc'):
                cases.append(({k: v for k, v in original.items() if k != field}, 'Full aware'))
                for value in (None, True, 123, [], '2026-11-25',
                              '2026-11-25T03:15:56', '2026-11-25T03:15:56.0000001+00:00'):
                    cases.append(({**original, field: value}, 'Full aware'))
            for metadata, reason in cases:
                path.write_text(json.dumps(metadata))
                before = {p: p.read_bytes() for p in target.iterdir()}
                for loader in (load_pk5_capture, load_pk5_snapshot):
                    with self.subTest(metadata=metadata, loader=loader.__name__), patch(
                        'gzip.open', side_effect=AssertionError('Unexpected payload read'),
                    ) as opened:
                        with self.assertRaisesRegex(ValueError, reason):
                            loader(path, day=DAY)
                        opened.assert_not_called()
                self.assertEqual({p: p.read_bytes() for p in target.iterdir()}, before)

    def test_valid_real_metadata_preserves_snapshot_and_original_capture_bytes(self):
        files = {p: p.read_bytes() for p in DIRECTORY.iterdir() if p.is_file()}
        metadata = json.loads((DIRECTORY / 'capture.json').read_text())
        capture = load_pk5_capture(DIRECTORY / 'capture.json', day=DAY)
        self.assertEqual(capture['raw'], gzip.decompress(files[DIRECTORY / metadata['raw_file']]))
        expected = prepare_pk5_snapshot(day=DAY.isoformat(), **capture)
        self.assertEqual(load_pk5_snapshot(DIRECTORY / 'capture.json', day=DAY), expected)
        self.assertEqual(expected['id'], metadata['snapshot_id'])
        self.assertEqual(len(expected['rows']), 24)
        self.assertEqual({p: p.read_bytes() for p in files}, files)

    def test_cli_missing_timestamp_has_validation_error_and_no_partial_report_in_both_orders(self):
        original = json.loads((DIRECTORY / 'capture.json').read_text())
        with TemporaryDirectory() as temporary:
            target = Path(temporary)
            path = target / 'capture.json'
            (target / original['raw_file']).write_bytes((DIRECTORY / original['raw_file']).read_bytes())
            for field in ('retrieved_at_utc', 'retrieval_completed_at_utc'):
                path.write_text(json.dumps({k: v for k, v in original.items() if k != field}))
                before = {p: p.read_bytes() for p in target.iterdir()}
                for ordered in ((DIRECTORY / 'capture.json', path), (path, DIRECTORY / 'capture.json')):
                    result = subprocess.run(
                        [sys.executable, str(ROOT / 'shadow_pk5_audit.py'), '--day', DAY.isoformat(),
                         '--require-selection', *map(str, ordered)], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertEqual(result.stdout, '')
                    self.assertIn('Full aware PK5 capture timestamp', result.stderr)
                    self.assertNotIn('Traceback', result.stderr)
                self.assertEqual({p: p.read_bytes() for p in target.iterdir()}, before)


if __name__ == '__main__':
    unittest.main()
