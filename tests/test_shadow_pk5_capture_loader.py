from datetime import date, datetime, timedelta
import gzip
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from shadow_asof import cutoff_for_day, select_snapshot
from shadow_pk5_snapshot import prepare_pk5_snapshot
from shadow_source_history import load_pk5_capture


FIXTURES = Path(__file__).parent / 'fixtures'
DAY = date(2026, 11, 22)
NAMES = ['pse_pk5_delivery_2026-11-22' + suffix
         for suffix in ('', '_second', '_third', '_fourth')]


class PK5CaptureLoaderTests(unittest.TestCase):
    def test_four_real_captures_validate_without_any_fix1_manifest(self):
        snapshots = []
        before = {}
        for name in NAMES:
            root = FIXTURES / name
            metadata = json.loads((root / 'capture.json').read_text())
            paths = [root / 'capture.json', root / metadata['raw_file']]
            before.update({path: path.read_bytes() for path in paths})
            capture = load_pk5_capture(root / 'capture.json', day=DAY)
            self.assertEqual(set(capture),
                             {'raw', 'source_url', 'retrieved_at', 'retrieval_completed_at'})
            self.assertEqual(sha256(capture['raw']).hexdigest(), metadata['raw_sha256'])
            self.assertEqual(capture['raw'], gzip.decompress(paths[1].read_bytes()))
            snapshot = prepare_pk5_snapshot(day=DAY.isoformat(), **capture)
            self.assertEqual(snapshot['id'], metadata['snapshot_id'])
            self.assertEqual(len(snapshot['rows']), 24)
            snapshots.append(snapshot)
        for candidates in (snapshots, list(reversed(snapshots))):
            selected = select_snapshot(DAY, candidates)
            self.assertEqual(selected.snapshot_id, snapshots[-1]['id'])
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_legacy_metadata_still_checks_cutoff_and_original_payload(self):
        root = FIXTURES / NAMES[-1]
        original = json.loads((root / 'capture.json').read_text())
        raw = (root / original['raw_file']).read_bytes()
        with TemporaryDirectory() as directory:
            target = Path(directory)
            (target / original['raw_file']).write_bytes(raw)
            path = target / 'capture.json'
            legacy = {key: value for key, value in original.items() if key != 'snapshot_id'}
            path.write_text(json.dumps(legacy))
            self.assertEqual(load_pk5_capture(path, day=DAY)['raw'], gzip.decompress(raw))
            for field in ('retrieved_at_utc', 'retrieval_completed_at_utc'):
                changed = {**legacy, field: (cutoff_for_day(DAY) +
                                             timedelta(microseconds=1)).isoformat()}
                path.write_text(json.dumps(changed))
                with self.subTest(field=field), self.assertRaises(ValueError):
                    load_pk5_capture(path, day=DAY)
            path.write_text(json.dumps({**legacy, 'raw_sha256': '0' * 64}))
            with self.assertRaisesRegex(ValueError, 'SHA-256'):
                load_pk5_capture(path, day=DAY)
            self.assertEqual((target / original['raw_file']).read_bytes(), raw)

    def test_invalid_day_identity_metadata_or_path_never_changes_evidence(self):
        root = FIXTURES / NAMES[-1]
        original = json.loads((root / 'capture.json').read_text())
        with TemporaryDirectory() as directory:
            target = Path(directory)
            raw_path = target / original['raw_file']
            raw_path.write_bytes((root / original['raw_file']).read_bytes())
            path = target / 'capture.json'
            for updates in (
                {'snapshot_id': None}, {'snapshot_id': 'pk5-capture-sha256:' + '0' * 64},
                {'delivery_date': '2026-11-21'}, {'http_status': True},
                {'response_url': 'https://example.com/'}, {'rows': 23},
                {'bytes': True}, {'raw_file': '../outside.json.gz'},
            ):
                path.write_text(json.dumps({**original, **updates}))
                before = {p: p.read_bytes() for p in target.iterdir()}
                with self.subTest(updates=updates), self.assertRaises(ValueError):
                    load_pk5_capture(path, day=DAY)
                self.assertEqual({p: p.read_bytes() for p in target.iterdir()}, before)
            for invalid in ('2026-11-22', datetime(2026, 11, 22), date.min, date.max):
                with self.subTest(day=invalid), self.assertRaisesRegex(
                        ValueError, 'Supported delivery date'):
                    load_pk5_capture(target / 'does-not-exist.json', day=invalid)


if __name__ == '__main__':
    unittest.main()
