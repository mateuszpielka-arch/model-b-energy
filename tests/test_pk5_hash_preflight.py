"""Reject malformed declared PK5 hashes before opening compressed evidence."""
from datetime import date
import gzip
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from shadow_source_history import load_pk5_capture, load_pk5_snapshot

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / 'tests/fixtures/pse_pk5_delivery_2026-11-28_fourth'
DAY = date(2026, 11, 28)
ERROR = 'PK5 capture raw size or SHA-256 mismatch'


class PK5HashPreflightTests(unittest.TestCase):
    def test_malformed_hashes_fail_before_evidence_path_access_in_both_loaders(self):
        original = json.loads((DIRECTORY / 'capture.json').read_text())
        missing = object()
        values = (missing, None, True, 123, [], {}, '', 'a' * 63, 'a' * 65,
                  'g' * 64, original['raw_sha256'].upper(),
                  original['raw_sha256'] + '\n', ' ' + original['raw_sha256'])
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / 'capture.json'
            for value in values:
                metadata = {**original, 'raw_sha256': value}
                if value is missing:
                    del metadata['raw_sha256']
                path.write_text(json.dumps(metadata))
                before = path.read_bytes()
                for loader in (load_pk5_capture, load_pk5_snapshot):
                    with self.subTest(value=value, loader=loader.__name__), patch(
                        'shadow_ingest_manifest._inside',
                        side_effect=AssertionError('Unexpected evidence path access')
                    ) as inside:
                        with self.assertRaisesRegex(ValueError, ERROR):
                            loader(path, day=DAY)
                        inside.assert_not_called()
                self.assertEqual(path.read_bytes(), before)

    def test_well_formed_wrong_digest_still_reads_and_verifies_original_bytes(self):
        files = {p: p.read_bytes() for p in DIRECTORY.iterdir() if p.is_file()}
        original = json.loads((DIRECTORY / 'capture.json').read_text())
        expected = load_pk5_snapshot(DIRECTORY / 'capture.json', day=DAY)
        with TemporaryDirectory() as temporary:
            target = Path(temporary)
            compressed = target / original['raw_file']
            compressed.write_bytes(files[DIRECTORY / original['raw_file']])
            path = target / 'capture.json'
            path.write_text(json.dumps({**original, 'raw_sha256': '0' * 64}))
            before = {p: p.read_bytes() for p in target.iterdir()}
            open_gzip = gzip.open
            for loader in (load_pk5_capture, load_pk5_snapshot):
                with self.subTest(loader=loader.__name__), patch(
                    'gzip.open', wraps=open_gzip
                ) as opened, patch(
                    'shadow_source_history.prepare_pk5_snapshot',
                    side_effect=AssertionError('Corrupt digest reached adapter')
                ) as prepared:
                    with self.assertRaisesRegex(ValueError, ERROR):
                        loader(path, day=DAY)
                    opened.assert_called_once()
                    prepared.assert_not_called()
                self.assertEqual({p: p.read_bytes() for p in target.iterdir()}, before)
            path.write_text(json.dumps(original))
            self.assertEqual(load_pk5_snapshot(path, day=DAY), expected)
            self.assertEqual(expected['id'], original['snapshot_id'])
            self.assertEqual(len(expected['rows']), 24)
            self.assertEqual(load_pk5_capture(path, day=DAY)['raw'],
                             gzip.decompress(compressed.read_bytes()))
        self.assertEqual({p: p.read_bytes() for p in files}, files)

    def test_cli_rejects_bad_hash_without_partial_json_even_without_payload(self):
        original = json.loads((DIRECTORY / 'capture.json').read_text())
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / 'capture.json'
            path.write_text(json.dumps({**original, 'raw_sha256': 'g' * 64}))
            before = path.read_bytes()
            # Deliberately no gzip: malformed hash metadata is rejected first.
            for ordered in ((DIRECTORY / 'capture.json', path), (path, DIRECTORY / 'capture.json')):
                result = subprocess.run(
                    [sys.executable, str(ROOT / 'shadow_pk5_audit.py'), '--day', DAY.isoformat(),
                     '--require-selection', *map(str, ordered)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')
                self.assertIn(ERROR, result.stderr)
                self.assertNotIn('Traceback', result.stderr)
                self.assertNotIn('Invalid gzip', result.stderr)
            self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
