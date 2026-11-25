"""Cutoff metadata rejects payload reads; synthetic boundary copies use real PK5 bytes."""
from datetime import date, timedelta
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from shadow_asof import cutoff_for_day
from shadow_source_history import load_pk5_capture, load_pk5_snapshot

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / 'tests/fixtures/pse_pk5_delivery_2026-11-26_twelfth'
DAY = date(2026, 11, 26)


def cases(original):
    cutoff = cutoff_for_day(DAY)
    late = (cutoff + timedelta(microseconds=1)).isoformat()
    return (
        ({**original, 'retrieved_at_utc': late, 'retrieval_completed_at_utc': late},
         'RETRIEVAL_STARTED_AFTER_CUTOFF'),
        ({**original, 'retrieval_completed_at_utc': late},
         'RETRIEVAL_COMPLETED_AFTER_CUTOFF'),
        ({**original, 'retrieved_at_utc': original['retrieval_completed_at_utc'],
          'retrieval_completed_at_utc': original['retrieved_at_utc']},
         'INVALID_RETRIEVAL_ORDER'),
    )


class PK5RetrievalGateTests(unittest.TestCase):
    def test_invalid_retrieval_metadata_never_opens_payload_for_either_loader(self):
        original = json.loads((DIRECTORY / 'capture.json').read_text())
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / 'capture.json'
            # No gzip file exists: the metadata error must take precedence over I/O.
            for metadata, reason in cases(original):
                path.write_text(json.dumps(metadata))
                before = path.read_bytes()
                for loader in (load_pk5_capture, load_pk5_snapshot):
                    with self.subTest(reason=reason, loader=loader.__name__), patch(
                        'gzip.open', side_effect=AssertionError('Unexpected payload read'),
                    ) as opened:
                        with self.assertRaisesRegex(ValueError, reason):
                            loader(path, day=DAY)
                        opened.assert_not_called()
                self.assertEqual(path.read_bytes(), before)

    def test_exact_cutoff_in_equivalent_zones_still_reads_and_validates_real_payload(self):
        original = json.loads((DIRECTORY / 'capture.json').read_text())
        files = {p: p.read_bytes() for p in DIRECTORY.iterdir() if p.is_file()}
        baseline = load_pk5_snapshot(DIRECTORY / 'capture.json', day=DAY)
        with TemporaryDirectory() as temporary:
            target = Path(temporary)
            path = target / 'capture.json'
            raw_path = target / original['raw_file']
            raw_path.write_bytes(files[DIRECTORY / original['raw_file']])
            snapshots = []
            for timestamp in ('2026-11-25T08:50:00+00:00', '2026-11-25T09:50:00+01:00'):
                metadata = {k: v for k, v in original.items() if k != 'snapshot_id'}
                metadata.update(retrieved_at_utc=timestamp, retrieval_completed_at_utc=timestamp)
                path.write_text(json.dumps(metadata))
                capture = load_pk5_capture(path, day=DAY)
                self.assertEqual(capture['retrieved_at'], cutoff_for_day(DAY))
                snapshot = load_pk5_snapshot(path, day=DAY)
                self.assertEqual(snapshot['rows'], baseline['rows'])
                snapshots.append(snapshot)
            self.assertEqual(snapshots[0], snapshots[1])
            self.assertNotEqual(snapshots[0]['id'], baseline['id'])
            # Passing the early gate cannot bypass the compressed-evidence integrity checks.
            raw_path.write_bytes(b'not gzip')
            with self.assertRaisesRegex(ValueError, 'Invalid gzip PK5 evidence'):
                load_pk5_snapshot(path, day=DAY)
        self.assertEqual({p: p.read_bytes() for p in files}, files)

    def test_cli_rejects_late_or_reversed_capture_without_partial_json_in_both_orders(self):
        original = json.loads((DIRECTORY / 'capture.json').read_text())
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / 'capture.json'
            for metadata, reason in cases(original):
                path.write_text(json.dumps(metadata))
                for ordered in ((DIRECTORY / 'capture.json', path), (path, DIRECTORY / 'capture.json')):
                    result = subprocess.run(
                        [sys.executable, str(ROOT / 'shadow_pk5_audit.py'), '--day', DAY.isoformat(),
                         '--require-selection', *map(str, ordered)], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(result.stdout, '')
                    self.assertIn(reason, result.stderr)
                    self.assertNotIn('Traceback', result.stderr)


if __name__ == '__main__':
    unittest.main()
